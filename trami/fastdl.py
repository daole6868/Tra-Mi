"""Tải video nhiều luồng (2.1.30) – như IDM / aria2: chia file thành nhiều khúc, mỗi khúc 1 kết nối (Range), tải cùng lúc
rồi ghép. yt-dlp tải mỗi file bằng 1 kết nối, hình xong mới tới tiếng; máy chủ video (Bilibili, Douyin, YouTube) hay giới
hạn tốc độ MỖI kết nối và mạng xa (Việt Nam ↔ máy chủ Trung Quốc) làm 1 kết nối TCP chậm vì độ trễ + mất gói -> nhiều kết
nối cùng lúc nhanh hơn nhiều lần. Bilibili còn có link dự phòng ở máy chủ khác: thử 1 MB đầu ở mỗi máy chủ, chọn máy chủ
nhanh nhất (như trình phát trên trang tự chọn). yt-dlp vẫn lấy link + chọn chất lượng; máy chủ không cho tải theo khúc ->
1 luồng; link hết hạn / bị từ chối -> `Expired` (người gọi để yt-dlp tải như cũ). Mất mạng -> chờ có mạng rồi tải tiếp
đúng khúc dở (file .svpart + .svpart.json ghi các khúc đã xong).

2.3.5: "đang tải gần xong thì lỗi HTTP Error 567" (Bilibili trả 567 khi tải quá nhiều kết nối / quá nhanh). Trước đây 1
khúc bị từ chối 6 lần là bỏ CẢ lần tải nhiều luồng, xoá file đã xong, để yt-dlp tải lại từ đầu – mà file tạm của yt-dlp
trùng tên ".part" với file dựng sẵn đủ cỡ của tool (khúc chưa tải = số 0) -> yt-dlp có thể coi là tải xong (video hỏng)
hoặc lại bị 567. Giờ: 429 / 5xx = `Busy` (máy chủ tạm từ chối) -> giảm số luồng, chờ lâu dần rồi tải tiếp; vẫn không
được thì giữ nguyên phần đã tải (tên riêng ".svpart") để lấy link mới / bấm Tải tiếp."""
from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from . import net
from .ffmpeg import Cancelled

CHUNK_MIN = 1 << 20          # mỗi khúc 1–16 MB (file lớn khúc to, đủ ~4 khúc / luồng để luồng nhanh làm thay luồng chậm)
CHUNK_MAX = 16 << 20
MIN_SPLIT = 3 << 20          # file nhỏ hơn: 1 luồng
RACE_BYTES = 1 << 20         # thử tốc độ mỗi máy chủ bằng 1 MB đầu
RACE_SEC = 6.0
RETRIES = 6                  # mỗi khúc thử lại tối đa (lỗi mạng khi đã mất mạng hẳn không tính – chờ có mạng)
BUSY_WAIT = (3, 6, 12, 20, 30, 45, 60, 60)   # máy chủ tạm từ chối (429 / 5xx): chờ lâu dần, hết lượt -> Busy
BUSY_CUT_SEC = 5.0           # giảm số luồng tối đa 1 lần / 5 s (các luồng cùng bị từ chối 1 lúc chỉ tính 1 lần)
PART = ".svpart"             # file đang tải (tên riêng – yt-dlp dùng ".part", không lẫn file dựng sẵn của tool)
READ = 1 << 18
TIMEOUT = (10, 20)           # nối / không nhận thêm dữ liệu 20 s -> thử lại khúc đó
# Bilibili: máy chủ P2P (PCDN) của người dùng khác – hay chậm / chập chờn khi tải từ nước ngoài -> xếp sau link upos
_PCDN = re.compile(r"mcdn\.bilivideo|szbdyd\.com|:\d{4,5}/v1/resource|\.mountaintoys\.cn", re.I)


class Expired(Exception):
    """Link hết hạn / bị từ chối / máy chủ bỏ qua Range giữa chừng -> để yt-dlp tải như cũ."""


class Busy(Expired):
    """Máy chủ tạm từ chối (HTTP 429 / 5xx – Bilibili 567): phần đã tải giữ nguyên, lấy link mới rồi tải tiếp."""

    def __init__(self, msg: str = "", status: int = 0):
        super().__init__(msg or f"HTTP {status}")
        self.status = status


def check_status(r) -> None:
    """401 / 403 / 404 / 410 -> Expired (link chết); 429 / 5xx (567…) -> Busy (thử lại sau); lỗi khác -> HTTPError."""
    if r.status_code in (401, 403, 404, 410):
        raise Expired(f"HTTP {r.status_code}")
    if r.status_code == 429 or r.status_code >= 500:
        raise Busy(f"HTTP {r.status_code}", r.status_code)
    r.raise_for_status()


def part_files(out: str | Path) -> tuple[Path, Path]:
    """(file đang tải, bảng khúc đã xong). File của bản 2.1.30–2.3.4 (".part" + ".part.json") -> đổi sang tên mới."""
    out = Path(out)
    part, meta = out.with_name(out.name + PART), out.with_name(out.name + PART + ".json")
    old, old_meta = out.with_name(out.name + ".part"), out.with_name(out.name + ".part.json")
    if old_meta.exists() and not meta.exists():   # có bảng khúc = file của tool (yt-dlp không ghi .part.json)
        try:
            if old.exists() and not part.exists():
                old.replace(part)
            old_meta.replace(meta)
        except OSError:
            pass
    return part, meta


def partial_bytes(out: str | Path) -> tuple[int, int]:
    """(số byte đã tải, cỡ file) của 1 file đang tải dở ((0, 0) nếu không có)."""
    out = Path(out)
    part, meta = part_files(out)
    try:
        m = json.loads(meta.read_text("utf-8"))
        size, chunk = int(m["size"]), int(m["chunk"])
        done = sum(min(size, (int(i) + 1) * chunk) - int(i) * chunk for i in m.get("done", []))
        return done, size
    except (OSError, ValueError, TypeError, KeyError):
        pass
    try:
        return (part.stat().st_size, 0) if part.exists() else (0, 0)
    except OSError:
        return 0, 0


def _wait(sec: float, cancel, stop: threading.Event | None = None) -> bool:
    """Chờ sec giây; False = bị Huỷ / dừng."""
    end = time.time() + sec
    while time.time() < end:
        if cancel() or (stop is not None and stop.is_set()):
            return False
        time.sleep(0.2)
    return True


def _get(url: str, headers: dict, cookies, rng: tuple[int, int] | None = None, timeout=TIMEOUT):
    h = dict(headers or {})
    h.pop("Range", None)
    h["Accept-Encoding"] = "identity"     # nén gzip làm lệch số byte của khúc
    if rng is not None:
        h["Range"] = f"bytes={rng[0]}-{rng[1]}"
    return net.session().get(url, headers=h, cookies=cookies, stream=True, timeout=timeout, allow_redirects=True)


def probe(url: str, headers: dict, cookies=None) -> tuple[int | None, bool, str]:
    """(cỡ file, máy chủ có cho tải theo khúc không, link sau chuyển hướng)."""
    r = _get(url, headers, cookies, (0, 0))
    try:
        check_status(r)
        if r.status_code == 206:
            m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
            size = int(m.group(1)) if m else None
            return size, size is not None, r.url
        size = int(r.headers.get("Content-Length") or 0) or None
        return size, False, r.url
    finally:
        r.close()


def order_hosts(urls: list[str]) -> list[str]:
    """Bỏ trùng; máy chủ P2P của Bilibili xếp sau."""
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return sorted(out, key=lambda u: bool(_PCDN.search(u)))


def race(urls: list[str], headers: dict, cookies=None, cancel: Callable[[], bool] | None = None,
         sec: float = RACE_SEC, nbytes: int = RACE_BYTES) -> list[str]:
    """Thử tải `nbytes` đầu từ mỗi link cùng lúc -> link xếp theo tốc độ (nhanh trước); link lỗi bỏ. Không link nào
    tải được -> giữ thứ tự cũ."""
    urls = order_hosts(urls)
    if len(urls) < 2:
        return urls
    speed: dict[str, float] = {}

    def one(u: str):
        t0, got = time.time(), 0
        try:
            r = _get(u, headers, cookies, (0, nbytes - 1), timeout=(5, sec))
            with r:
                if r.status_code not in (200, 206):
                    return
                for c in r.iter_content(65536):
                    got += len(c)
                    if got >= nbytes or time.time() - t0 > sec or (cancel and cancel()):
                        break
            if got:
                speed[u] = got / max(1e-3, time.time() - t0)
        except Exception:  # noqa: BLE001 – máy chủ lỗi: bỏ
            pass
    ts = [threading.Thread(target=one, args=(u,), daemon=True, name="trami-dl-race") for u in urls[:6]]
    for t in ts:
        t.start()
    end = time.time() + sec + 6
    for t in ts:
        t.join(max(0.0, end - time.time()))
    ok = sorted(speed, key=lambda u: -speed[u])
    return ok + [u for u in urls if u not in speed] if ok else urls


def chunk_size(size: int, conns: int) -> int:
    return int(min(CHUNK_MAX, max(CHUNK_MIN, size // max(1, conns * 4))))


class _Net:
    """Mất mạng: 1 luồng đi kiểm tra / chờ có mạng, các luồng khác chờ cùng (không mỗi luồng 1 vòng chờ)."""

    def __init__(self, cancel, report):
        self.lock = threading.Lock()
        self.cancel, self.report = cancel, report

    def wait(self) -> bool:
        with self.lock:
            return net.wait_online("tải video", cancel=self.cancel, report=self.report)


def fetch(urls: list[str], headers: dict, cookies, out: str | Path, conns: int = 8,
          on_bytes: Callable[[int, int], None] | None = None, cancel: Callable[[], bool] | None = None,
          report: Callable[[str], None] | None = None) -> Path:
    """Tải 1 file (link đầu tiên tải được trong `urls` – nhiều link thì chọn máy chủ nhanh nhất) về `out`.
    on_bytes(số byte vừa nhận – có thể âm khi 1 khúc phải tải lại, tổng cỡ file)."""
    out = Path(out)
    cancel = cancel or (lambda: False)
    nt = _Net(cancel, report)
    part, meta = part_files(out)
    if len(urls) > 1:
        if report:
            report("Đang chọn máy chủ nhanh nhất…")
        urls = race(urls, headers, cookies, cancel)
    last: Exception | None = None
    busy: Busy | None = None
    for url in urls:
        try:
            size, ranges, url = _retry(lambda u=url: probe(u, headers, cookies), nt, cancel)
            if not ranges or not size or size < MIN_SPLIT or conns <= 1:
                _single(url, headers, cookies, part, size, ranges, on_bytes, cancel, nt)
            else:
                _multi(url, headers, cookies, part, size, conns, on_bytes, cancel, nt)
            part.replace(out)
            meta.unlink(missing_ok=True)
            return out
        except (Cancelled, net.NetworkDown):
            raise
        except Exception as e:  # noqa: BLE001 – máy chủ này hỏng -> thử link dự phòng (file đang tải giữ nguyên)
            last = e
            if isinstance(e, Busy):
                busy = e
    if busy is not None:     # có máy chủ tạm từ chối -> người gọi lấy link mới rồi tải tiếp phần còn thiếu
        raise busy
    raise last if isinstance(last, Expired) else Expired(str(last or "không tải được"))


def _retry(fn, nt: _Net, cancel):
    tries = busy = 0
    while True:
        try:
            return fn()
        except Busy as e:
            if busy >= len(BUSY_WAIT):
                raise
            w = BUSY_WAIT[busy]
            busy += 1
            if nt.report:
                nt.report(f"Máy chủ video tạm từ chối ({e}) – chờ {w} s rồi thử lại")
            if not _wait(w, cancel):
                raise Cancelled() from None
        except Expired:
            raise
        except Exception as e:  # noqa: BLE001
            if cancel():
                raise Cancelled() from None
            if net.is_net_error(e) and nt.wait():
                continue
            tries += 1
            if tries > RETRIES:
                raise
            net.sleep(min(8.0, 0.5 * 2 ** tries), cancel)


def _single(url, headers, cookies, part: Path, size, ranges: bool, on_bytes, cancel, nt: _Net) -> None:
    """1 luồng: nối tiếp file đang tải khi máy chủ cho Range, không thì tải lại từ đầu."""
    tries = busy = 0
    while True:
        have = part.stat().st_size if (ranges and part.exists()) else 0
        if size and have >= size:
            return
        if on_bytes and have:
            on_bytes(have, size or 0)
        try:
            r = _get(url, headers, cookies, (have, (size or 0) - 1) if (have and size) else None)
            with r:
                check_status(r)
                if have and r.status_code != 206:
                    have = 0
                with open(part, "ab" if have else "wb") as f:
                    for c in r.iter_content(READ):
                        if cancel():
                            raise Cancelled()
                        f.write(c)
                        if on_bytes:
                            on_bytes(len(c), size or 0)
            if not size or part.stat().st_size >= size:
                return
            raise OSError("máy chủ ngắt giữa chừng")
        except Busy as e:
            if on_bytes and have:
                on_bytes(-have, size or 0)
            if busy >= len(BUSY_WAIT):
                raise
            w = BUSY_WAIT[busy]
            busy += 1
            if nt.report:
                nt.report(f"Máy chủ video tạm từ chối ({e}) – chờ {w} s rồi tải tiếp")
            if not _wait(w, cancel):
                raise Cancelled() from None
            continue
        except (Expired, Cancelled):
            raise
        except Exception as e:  # noqa: BLE001
            got = part.stat().st_size - have if part.exists() else 0
            if on_bytes:
                on_bytes(-(have + max(0, got)), size or 0)   # lần sau báo lại từ đầu phần đã có
            if net.is_net_error(e) and nt.wait():
                continue
            tries += 1
            if tries > RETRIES:
                raise
            net.sleep(min(8.0, 0.5 * 2 ** tries), cancel)


def _multi(url, headers, cookies, part: Path, size: int, conns: int, on_bytes, cancel, nt: _Net) -> None:
    meta = part.with_name(part.name + ".json")
    chunk = chunk_size(size, conns)
    done: set[int] = set()
    try:
        m = json.loads(meta.read_text("utf-8"))
        if m.get("size") == size and int(m.get("chunk") or 0) > 0 and part.exists() and part.stat().st_size == size:
            chunk = int(m["chunk"])   # tải tiếp: giữ cỡ khúc cũ – đổi số luồng cũng không mất phần đã tải (2.3.5)
            done = {int(i) for i in m.get("done", []) if 0 <= int(i) < -(-size // chunk)}
    except (OSError, ValueError, TypeError):
        pass
    n = -(-size // chunk)
    if not (part.exists() and part.stat().st_size == size):
        done = set()
        with open(part, "wb") as f:
            f.truncate(size)
    q = deque(i for i in range(n) if i not in done)
    lock = threading.Lock()
    stop = threading.Event()
    halted = threading.Event()     # 1 luồng thấy Huỷ (nút Huỷ của người gọi có thể không giữ trạng thái)
    errors: list[Exception] = []
    lim = {"n": max(1, min(conns, len(q))), "cut": 0.0}   # số luồng được chạy – máy chủ tạm từ chối thì giảm
    if on_bytes and done:
        on_bytes(sum(min(size, (i + 1) * chunk) - i * chunk for i in done), size)

    def save():
        try:
            with lock:
                d = sorted(done)
            meta.write_text(json.dumps({"size": size, "chunk": chunk, "done": d}), "utf-8")
        except OSError:
            pass

    def slow_down(e: Busy, btries: int) -> int:
        """Máy chủ tạm từ chối (Bilibili 567…): giảm một nửa số luồng (tối đa 1 lần / 5 s), chờ lâu dần."""
        with lock:
            if time.time() - lim["cut"] > BUSY_CUT_SEC and lim["n"] > 1:
                lim["n"] = max(1, lim["n"] // 2)
                lim["cut"] = time.time()
            k = lim["n"]
        w = BUSY_WAIT[min(btries, len(BUSY_WAIT) - 1)]
        if nt.report:
            nt.report(f"Máy chủ video tạm từ chối ({e}) – giảm còn {k} luồng, chờ {w} s rồi tải tiếp")
        return w

    def worker(idx: int):
        with open(part, "r+b") as f:
            while not stop.is_set():
                if idx >= lim["n"]:       # đã giảm số luồng -> luồng này nghỉ
                    return
                with lock:
                    if not q:
                        return
                    i = q.popleft()
                a, b = i * chunk, min(size, (i + 1) * chunk) - 1
                tries = btries = 0
                while True:
                    if cancel() or stop.is_set():
                        if not stop.is_set():
                            halted.set()
                        with lock:
                            q.appendleft(i)
                        return
                    got = 0
                    try:
                        r = _get(url, headers, cookies, (a, b))
                        with r:
                            check_status(r)
                            if r.status_code != 206:
                                raise Expired("máy chủ không trả đúng khúc")
                            f.seek(a)
                            for c in r.iter_content(READ):
                                if cancel() or stop.is_set():
                                    if not stop.is_set():
                                        halted.set()
                                    if on_bytes and got:
                                        on_bytes(-got, size)
                                    with lock:
                                        q.appendleft(i)
                                    return
                                c = c[:b - a + 1 - got]
                                f.write(c)
                                got += len(c)
                                if on_bytes:
                                    on_bytes(len(c), size)
                                if got > b - a:
                                    break
                        if got != b - a + 1:
                            raise OSError(f"khúc {i} thiếu dữ liệu ({got}/{b - a + 1})")
                        with lock:
                            done.add(i)
                        break
                    except Busy as e:
                        if on_bytes and got:
                            on_bytes(-got, size)
                        if btries >= len(BUSY_WAIT):
                            errors.append(e)
                            stop.set()
                            return
                        w = slow_down(e, btries)
                        btries += 1
                        if idx >= lim["n"]:          # luồng này bị cắt -> trả khúc cho luồng còn chạy
                            with lock:
                                q.appendleft(i)
                            return
                        if not _wait(w, cancel, stop):
                            if not stop.is_set():
                                halted.set()
                            with lock:
                                q.appendleft(i)
                            return
                    except Expired as e:
                        errors.append(e)
                        stop.set()
                        return
                    except Exception as e:  # noqa: BLE001
                        if on_bytes and got:
                            on_bytes(-got, size)
                        if net.is_net_error(e):
                            try:
                                if nt.wait():
                                    continue
                            except Exception as e2:  # noqa: BLE001 – Huỷ / mất mạng quá lâu
                                errors.append(e2)
                                stop.set()
                                return
                        tries += 1
                        if tries > RETRIES:
                            errors.append(e)
                            stop.set()
                            return
                        time.sleep(min(10.0, 0.5 * 2 ** tries))

    ts = [threading.Thread(target=worker, args=(k,), daemon=True, name="trami-dl") for k in range(lim["n"])]
    for t in ts:
        t.start()
    last = time.time()
    while any(t.is_alive() for t in ts):
        for t in ts:
            t.join(0.25)
        if cancel() or halted.is_set():
            stop.set()
            for t in ts:
                t.join(5)
            save()
            raise Cancelled()
        if time.time() - last > 2:
            last = time.time()
            save()
    save()
    if cancel() or halted.is_set():   # các luồng thấy Huỷ và dừng trước khi vòng chờ kịp xét
        raise Cancelled()
    if errors:
        e = errors[0]
        if isinstance(e, (Cancelled, net.NetworkDown, Expired)):
            raise e
        raise Expired(f"tải nhiều luồng lỗi: {e}")
    if len(done) != n:
        raise Expired(f"còn {n - len(done)} khúc chưa tải")


# ------------------------------------------------------------------ link dự phòng của Bilibili
BACKUPS: dict[str, list[str]] = {}   # link chính (baseUrl) -> link dự phòng (backupUrl – máy chủ khác)


def patch_bili_backups() -> None:
    """yt-dlp chỉ lấy link chính của mỗi dòng hình / tiếng Bilibili; ghi lại cả link dự phòng (cùng câu trả lời của trang,
    không hỏi thêm gì) để `race` chọn máy chủ nhanh nhất. Bản yt-dlp khác cách viết -> để nguyên."""
    try:
        from yt_dlp.extractor import bilibili as bl
    except Exception:  # noqa: BLE001
        return
    base = getattr(bl, "BilibiliBaseIE", None)
    orig = getattr(base, "extract_formats", None)
    if orig is None or getattr(orig, "_trami", False):
        return

    def extract_formats(self, play_info):
        fmts = orig(self, play_info)
        try:
            dash = (play_info or {}).get("dash") or {}
            items = list(dash.get("video") or []) + list(dash.get("audio") or []) + \
                list((dash.get("dolby") or {}).get("audio") or [])
            flac = (dash.get("flac") or {}).get("audio")
            if isinstance(flac, dict):
                items.append(flac)
            for it in items:
                if not isinstance(it, dict):
                    continue
                u = it.get("baseUrl") or it.get("base_url") or it.get("url")
                bk = [x for x in (it.get("backupUrl") or it.get("backup_url") or []) if isinstance(x, str)]
                if u and bk:
                    BACKUPS[u] = bk
        except Exception:  # noqa: BLE001 – chỉ là thêm link dự phòng
            pass
        return fmts

    extract_formats._trami = True
    base.extract_formats = extract_formats
