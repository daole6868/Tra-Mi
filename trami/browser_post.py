"""Đăng video kiểu "Mở Chrome & điền sẵn": tool mở Chrome / Edge đã cài trên máy (hồ sơ riêng của Subvideo – bạn đăng
nhập tay 1 lần), vào trang tải lên của YouTube Studio / Meta Business Suite / TikTok Studio, gắn file video, điền tiêu đề
/ mô tả / hashtag, chọn khán giả / quyền riêng tư… rồi DỪNG ở nút Đăng (viền cam) để bạn xem lại và tự bấm.

- Trang đổi giao diện -> bước nào không tìm thấy thì bỏ qua và báo "điền tay", không dừng cả việc.
- Playwright điều khiển trình duyệt ở 1 luồng riêng (`_Engine`): mọi lệnh tới trình duyệt chạy trên luồng đó; nhiều nền
  tảng cùng hồ sơ -> nhiều tab trong 1 cửa sổ.
- Đăng nhập: mở Chrome THƯỜNG (không bị điều khiển) với hồ sơ đó (`open_login`) – Google không cho đăng nhập trong trình
  duyệt do chương trình điều khiển; đăng nhập xong đóng cửa sổ, lần sau tool dùng lại phiên đăng nhập.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import net
from .config import DATA_DIR
from .publish import THUMB_HOWTO, PublishError, compose_text, load_accounts, reveal, save_accounts

YT_URL = "https://www.youtube.com/upload"
FB_URLS = {"video": "https://business.facebook.com/latest/composer",
           "reel": "https://business.facebook.com/latest/reels_composer"}
TT_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
LOGIN_URLS = {"youtube": "https://studio.youtube.com", "facebook": "https://business.facebook.com",
              "tiktok": "https://www.tiktok.com/login"}
HEADLESS = False                 # test chạy ẩn
INSTALL_CMD = r".venv\Scripts\python -m pip install playwright"
WAIT_FILE = 45                   # chờ trang tải lên hiện ô chọn file (giây)
WAIT_FORM = 150                  # chờ ô tiêu đề / chú thích hiện ra sau khi gắn file (video lớn đọc lâu)

# bộ chọn: thử lần lượt, cái nào có trước dùng cái đó (giao diện đổi thì thêm bộ chọn mới vào đầu danh sách)
YT = {
    "file": ['ytcp-uploads-file-picker input[type="file"]', 'input[type="file"][name="Filedata"]',
             'input[type="file"]'],
    "title": ['#title-textarea #textbox', '#title-textarea [contenteditable="true"]'],
    "desc": ['#description-textarea #textbox', '#description-textarea [contenteditable="true"]'],
    "thumb": ['ytcp-thumbnails-compact-editor-uploader input[type="file"]', 'input#file-loader',
              'ytcp-thumbnail-uploader input[type="file"]', 'ytcp-video-thumbnail-editor input[type="file"]',
              'ytcp-uploads-dialog input[type="file"][accept*="image"]'],
    "kids_no": ['[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]'],
    "kids_yes": ['[name="VIDEO_MADE_FOR_KIDS_MFK"]'],
    "ai_yes": ['[name="VIDEO_HAS_ALTERED_CONTENT_YES"]'],
    "ai_no": ['[name="VIDEO_HAS_ALTERED_CONTENT_NO"]'],
    "next": ['#next-button'],
    "public": ['tp-yt-paper-radio-button[name="PUBLIC"]', '[name="PUBLIC"]'],
    "unlisted": ['tp-yt-paper-radio-button[name="UNLISTED"]', '[name="UNLISTED"]'],
    "private": ['tp-yt-paper-radio-button[name="PRIVATE"]', '[name="PRIVATE"]'],
    "schedule": ['#second-container-expand-button'],
    "done": ['#done-button'],
    "link": ['ytcp-uploads-dialog a[href*="youtu.be/"]', 'ytcp-uploads-dialog a[href*="/shorts/"]',
             'a[href*="youtu.be/"]'],
    "published": ['ytcp-video-share-dialog', 'ytcp-uploads-still-processing-dialog'],
}
FB = {
    "file": ['input[type="file"][accept*="video"]', 'input[type="file"]'],
    "add": [r"Thêm video", r"Tải video lên", r"Add video", r"Upload video", r"Thêm ảnh/video", r"Add photo/video"],
    "title": ['input[placeholder*="iêu đề"]', 'input[placeholder*="itle"]', 'input[aria-label*="iêu đề"]',
              'input[aria-label*="itle"]'],
    "text": ['[contenteditable="true"][role="textbox"]', 'div[contenteditable="true"]', 'textarea'],
    "post": [r"^Đăng$", r"^Chia sẻ ngay$", r"^Đăng ngay$", r"^Publish$", r"^Share now$", r"^Post$", r"^Lên lịch$",
             r"^Schedule$"],
}
TT = {
    "file": ['input[type="file"][accept*="video"]', 'input[type="file"]'],
    "cover_edit": [r"Edit cover", r"Sửa ảnh bìa", r"Chỉnh sửa ảnh bìa", r"Chọn ảnh bìa", r"Select cover"],
    "cover_upload": [r"Upload cover", r"Tải ảnh bìa lên", r"Tải lên ảnh bìa", r"^Tải lên$", r"^Upload$"],
    "cover_file": ['[role="dialog"] input[type="file"][accept*="image"]', 'input[type="file"][accept*="image"]'],
    "cover_ok": [r"^Confirm$", r"^Xác nhận$", r"^Lưu$", r"^Save$", r"^Done$", r"^Xong$"],
    "caption": ['.public-DraftEditor-content[contenteditable="true"]', '.DraftEditor-root [contenteditable="true"]',
                'div[contenteditable="true"]'],
    "post": ['button[data-e2e="post_video_button"]'],
    "post_text": [r"^Đăng$", r"^Post$"],
}
LOGIN_HINTS = ("accounts.google.com", "/login", "facebook.com/login", "/checkpoint", "signin")


# ------------------------------------------------------------------ hồ sơ trình duyệt + tìm Chrome
def profiles() -> list[dict]:
    return load_accounts("browser") or [{"id": "main", "name": "Hồ sơ 1"}]


def add_profile(name: str) -> dict:
    items = profiles()
    p = {"id": "p" + uuid.uuid4().hex[:8], "name": name.strip() or f"Hồ sơ {len(items) + 1}"}
    save_accounts("browser", items + [p])
    return p


def rename_profile(pid: str, name: str) -> None:
    save_accounts("browser", [dict(x, name=name.strip() or x["name"]) if x["id"] == pid else x for x in profiles()])


def remove_profile(pid: str) -> None:
    """Bỏ hồ sơ + xoá thư mục (mất đăng nhập đã lưu trong hồ sơ đó)."""
    left = [x for x in profiles() if x["id"] != pid]
    save_accounts("browser", left or [{"id": "main", "name": "Hồ sơ 1"}])
    shutil.rmtree(profile_dir(pid), ignore_errors=True)


def profile_dir(pid: str) -> Path:
    d = DATA_DIR / "browser" / re.sub(r"[^\w-]", "_", pid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_browser(settings: dict | None = None) -> str:
    """Chrome (ưu tiên) hoặc Edge đã cài; Cài đặt `browser_exe` để chỉ định file khác."""
    own = ((settings or {}).get("browser_exe") or os.environ.get("TRAMI_BROWSER") or "").strip()
    if own and Path(own).exists():
        return own
    cands = []
    if sys.platform.startswith("win"):
        for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
            if base:
                cands.append(Path(base) / "Google/Chrome/Application/chrome.exe")
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES")):
            if base:
                cands.append(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    elif sys.platform == "darwin":
        cands += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    for c in cands:
        if c.exists():
            return str(c)
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser", "microsoft-edge"):
        w = shutil.which(name)
        if w:
            return w
    return ""


def problem(settings: dict | None = None) -> str:
    """'' = dùng được; không thì lý do + cách sửa."""
    if importlib.util.find_spec("playwright") is None:
        return f"Chưa cài Playwright – mở cửa sổ lệnh ở thư mục tool, chạy: {INSTALL_CMD}"
    if not find_browser(settings):
        return "Không tìm thấy Chrome / Edge – cài Google Chrome hoặc chỉ đường dẫn chrome.exe ở Cài đặt."
    return ""


def open_login(settings: dict, pid: str, platform: str) -> str:
    """Mở Chrome THƯỜNG (không bị điều khiển) với hồ sơ của tool ở trang đăng nhập – Google / Facebook / TikTok cho đăng
    nhập bình thường; đăng nhập xong đóng cửa sổ này. Trả đường dẫn trình duyệt."""
    exe = find_browser(settings)
    if not exe:
        raise PublishError(problem(settings))
    if engine().close_profile(pid):        # cửa sổ do tool điều khiển đang giữ hồ sơ -> đóng, chờ Chrome nhả hồ sơ
        time.sleep(1.5)
    subprocess.Popen([exe, f"--user-data-dir={profile_dir(pid)}", "--no-first-run", "--no-default-browser-check",
                      LOGIN_URLS[platform]], close_fds=True)
    return exe


# ------------------------------------------------------------------ luồng điều khiển trình duyệt
class _Engine:
    """1 luồng giữ Playwright + các cửa sổ (mỗi hồ sơ 1 cửa sổ); việc khác gửi lệnh qua hàng đợi."""

    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.pw = None
        self.ctx: dict[str, object] = {}
        self.pages: dict[str, tuple] = {}     # mã tab -> (page, nền tảng, báo cáo)

    def call(self, fn, *a, timeout: float | None = None):
        """Chạy fn(engine, *a) trên luồng trình duyệt, trả kết quả (lỗi thì ném lại ở luồng gọi)."""
        with self.lock:
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._loop, daemon=True, name="trami-browser")
                self.thread.start()
        fut: concurrent.futures.Future = concurrent.futures.Future()
        self.q.put((fn, a, fut))
        return fut.result(timeout)

    def _loop(self):
        while True:
            fn, a, fut = self.q.get()
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                if self.pw is None:
                    from playwright.sync_api import sync_playwright

                    self.pw = sync_playwright().start()
                fut.set_result(fn(self, *a))
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)

    # ---- chạy trên luồng trình duyệt
    def context(self, settings: dict, pid: str):
        c = self.ctx.get(pid)
        if c is not None:
            try:
                c.pages            # còn sống?
                if not getattr(c, "_trami_closed", False):
                    return c
            except Exception:  # noqa: BLE001
                pass
        exe = find_browser(settings)
        try:
            c = self.pw.chromium.launch_persistent_context(
                str(profile_dir(pid)), executable_path=exe or None, headless=HEADLESS, no_viewport=True,
                args=["--start-maximized", "--no-first-run", "--no-default-browser-check"])
        except Exception as e:  # noqa: BLE001
            low = str(e).lower()
            if "in use" in low or "existing browser" in low or "closed" in low or "exited" in low:
                raise PublishError("Chrome của hồ sơ này đang mở (cửa sổ đăng nhập?) – đóng hết cửa sổ Chrome của hồ "
                                   "sơ đó rồi bấm Đăng lại.") from e
            raise PublishError(f"Không mở được trình duyệt {exe}: {str(e)[:300]}") from e
        c.on("close", lambda *_: setattr(c, "_trami_closed", True))
        self.ctx[pid] = c
        return c

    def close_profile(self, pid: str) -> bool:
        """Đóng cửa sổ của hồ sơ (nếu tool đang mở). True = đã có cửa sổ bị đóng."""
        def run(eng):
            c = eng.ctx.pop(pid, None)
            if c is not None:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass
        if pid in self.ctx:
            self.call(run)
            return True
        return False


_engine: _Engine | None = None


def engine() -> _Engine:
    global _engine
    if _engine is None:
        _engine = _Engine()
    return _engine


# ------------------------------------------------------------------ các bước điền (chạy trên luồng trình duyệt)
def _first(page, sels: list[str], timeout: float, attached: bool = False):
    """Phần tử đầu tiên khớp 1 trong các bộ chọn (tìm cả trong khung con); hết giờ -> None."""
    end = time.time() + timeout
    while True:
        for fr in page.frames:
            for sel in sels:
                try:
                    loc = fr.locator(sel).first
                    if loc.count() and (attached or loc.is_visible()):
                        return loc
                except Exception:  # noqa: BLE001
                    continue
        if time.time() >= end:
            return None
        page.wait_for_timeout(300)


def _button(page, names: list[str], timeout: float):
    """Nút có chữ khớp 1 trong các mẫu (không phân biệt hoa thường)."""
    end = time.time() + timeout
    while True:
        for n in names:
            try:
                loc = page.get_by_role("button", name=re.compile(n, re.I)).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001
                continue
        if time.time() >= end:
            return None
        page.wait_for_timeout(300)


def _type(page, loc, text: str):
    """Thay nội dung ô: ô thường -> fill; ô soạn thảo (contenteditable) -> bấm vào, chọn hết, xoá, gõ từng dòng."""
    tag = ""
    try:
        tag = (loc.evaluate("e => e.tagName") or "").lower()
    except Exception:  # noqa: BLE001
        pass
    if tag in ("input", "textarea"):
        loc.fill(text)
        return
    loc.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    for i, line in enumerate(text.split("\n")):
        if i:
            page.keyboard.press("Enter")
        if line:
            page.keyboard.insert_text(line)


def _thumb_left(item: dict, rep: "_Report") -> str:
    """Ảnh bìa tool không gắn được -> chỗ đặt trên trang + tên file (thư mục chứa ảnh sẽ được mở sẵn)."""
    th = item.get("thumbnail")
    if not th or "ảnh bìa" not in rep.miss:
        return ""
    return f"Ảnh bìa: {THUMB_HOWTO[(item['platform'], item.get('kind', 'video'))]} – kéo file {Path(th).name} vào."


def _mark(page, loc, note: str):
    """Viền cam nút Đăng + dòng nhắc nhỏ ở góc trang (không bấm hộ)."""
    try:
        loc.scroll_into_view_if_needed(timeout=3000)
        loc.evaluate("e => { e.style.outline = '3px solid #ff6a3d'; e.style.outlineOffset = '3px'; }")
    except Exception:  # noqa: BLE001
        pass
    try:
        page.evaluate("""m => { let d = document.getElementById('subvideo-note'); if (!d) { d = document.createElement('div');
            d.id = 'subvideo-note'; document.body.appendChild(d); }
            d.textContent = m; d.style.cssText = 'position:fixed;left:16px;bottom:16px;z-index:2147483647;max-width:460px;' +
            'background:#ff6a3d;color:#fff;font:600 14px/1.4 sans-serif;padding:10px 14px;border-radius:10px;' +
            'box-shadow:0 4px 18px rgba(0,0,0,.35);pointer-events:none';
            setTimeout(() => d.remove(), 60000); }""", note)
    except Exception:  # noqa: BLE001
        pass


class _Report:
    def __init__(self):
        self.done: list[str] = []
        self.miss: list[str] = []
        self.url = ""
        self.need_login = False
        self.note = ""

    def step(self, name: str, fn) -> bool:
        """Chạy 1 bước; lỗi / không thấy -> ghi "điền tay", không dừng các bước sau."""
        try:
            ok = fn()
        except Exception:  # noqa: BLE001
            ok = False
        (self.done if ok is not False else self.miss).append(name)
        return ok is not False


def _logged_out(page) -> bool:
    return any(h in page.url for h in LOGIN_HINTS)


def _fill_youtube(page, item: dict, rep: _Report):
    page.goto(YT_URL, wait_until="domcontentloaded", timeout=60000)
    f = _first(page, YT["file"], WAIT_FILE, attached=True)
    if f is None:
        rep.need_login = _logged_out(page)
        rep.miss.append("gắn file video")
        return
    f.set_input_files(item["file"])
    rep.done.append("gắn file video")
    title = (item.get("title") or "").replace("<", "").replace(">", "")[:100]
    desc = compose_text(item, "youtube", item.get("kind", "video")).replace("<", "").replace(">", "")

    def write(sel, text, wait):
        loc = _first(page, YT[sel], wait)
        if loc is None:
            return False
        _type(page, loc, text)
    if title:
        rep.step("tiêu đề", lambda: write("title", title, WAIT_FORM))
    if desc:
        rep.step("mô tả", lambda: write("desc", desc, 20))
    th = item.get("thumbnail")
    if th and Path(th).exists():
        rep.step("ảnh bìa", lambda: _set_file(page, YT["thumb"], th, 15))
    rep.step("khán giả (trẻ em)", lambda: _click(page, YT["kids_yes" if item.get("made_for_kids") else "kids_no"]))
    if item.get("synthetic"):
        rep.step("nội dung AI", lambda: _click(page, YT["ai_yes"], 4))
    for i in range(3):                       # Chi tiết -> Thành phần -> Kiểm tra -> Chế độ hiển thị
        if not rep.step(f"Tiếp ({i + 1}/3)", lambda: _click(page, YT["next"], 20, enabled=True)):
            break
        page.wait_for_timeout(700)
    if item.get("publish_at"):
        rep.step("mở mục Lên lịch", lambda: _click(page, YT["schedule"], 8))
        rep.note = f"chọn ngày giờ đăng {_when_text(item['publish_at'])} ở mục Lên lịch"
    else:
        rep.step("chế độ hiển thị", lambda: _click(page, YT[item.get("privacy") or "public"], 10))
    link = _first(page, YT["link"], 5)
    if link is not None:
        try:
            rep.url = link.get_attribute("href") or link.inner_text()
        except Exception:  # noqa: BLE001
            pass
    done = _first(page, YT["done"], 8)
    if done is not None:
        _mark(page, done, " ".join(x for x in ("Subvideo đã điền sẵn – kiểm tra lại rồi bấm nút viền cam (Xuất bản / "
                                               "Lên lịch).", _thumb_left(item, rep)) if x))


def _fill_facebook(page, item: dict, rep: _Report):
    kind = item.get("kind", "video")
    page.goto(FB_URLS["reel" if kind == "reel" else "video"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    if _logged_out(page):
        rep.need_login = True
        rep.miss.append("gắn file video")
        return
    if not _attach(page, FB, item["file"]):
        rep.miss.append("gắn file video")
    else:
        rep.done.append("gắn file video")
    text = compose_text(item, "facebook", kind, with_title=kind != "reel")
    if kind != "reel" and item.get("title"):
        tl = _first(page, FB["title"], 6)
        if tl is not None:
            rep.step("tiêu đề", lambda: _type(page, tl, item["title"][:250]))
            text = compose_text(item, "facebook", kind)
    if text:
        rep.step("nội dung bài đăng", lambda: _write_box(page, FB["text"], text, WAIT_FORM))
    if item.get("thumbnail"):
        rep.miss.append("ảnh bìa")    # không tự gắn: ô chọn file của trang nhận cả ảnh -> dễ thành ảnh của bài đăng
    if item.get("publish_at"):
        rep.note = f"chọn Lên lịch {_when_text(item['publish_at'])}"
    rep.note = "; ".join(x for x in (rep.note, "chọn đúng Trang ở góc trên nếu tool mở nhầm Trang") if x)
    post = _button(page, FB["post"], 6)
    if post is not None:
        _mark(page, post, " ".join(x for x in ("Subvideo đã điền sẵn – kiểm tra Trang + nội dung rồi bấm nút viền cam.",
                                               _thumb_left(item, rep)) if x))


def _fill_tiktok(page, item: dict, rep: _Report):
    page.goto(TT_URL, wait_until="domcontentloaded", timeout=60000)
    f = _first(page, TT["file"], WAIT_FILE, attached=True)
    if f is None:
        rep.need_login = _logged_out(page)
        rep.miss.append("gắn file video")
        return
    f.set_input_files(item["file"])
    rep.done.append("gắn file video")
    text = compose_text(item, "tiktok", "video", with_title=not (item.get("description") or "").strip())[:2200]
    if text:
        rep.step("chú thích + hashtag", lambda: _write_box(page, TT["caption"], text, WAIT_FORM))
    th = item.get("thumbnail")
    if th and Path(th).exists():
        rep.step("ảnh bìa", lambda: _tiktok_cover(page, th))
    if item.get("publish_at"):
        rep.note = "; ".join(x for x in (rep.note, f"bật Lên lịch và chọn {_when_text(item['publish_at'])}") if x)
    post = _first(page, TT["post"], 8) or _button(page, TT["post_text"], 3)
    if post is not None:
        _mark(page, post, " ".join(x for x in ("Subvideo đã điền sẵn – kiểm tra rồi bấm nút viền cam (Đăng).",
                                               _thumb_left(item, rep)) if x))


def _tiktok_cover(page, path) -> bool:
    """Ảnh bìa TikTok: Sửa ảnh bìa -> Tải ảnh bìa lên -> chọn file -> Xác nhận. Bước nào không thấy -> đóng hộp, báo
    "điền tay"."""
    edit = _text_target(page, TT["cover_edit"], 20)
    if edit is None:
        return False
    edit.click()
    try:
        up = _text_target(page, TT["cover_upload"], 8)
        if up is not None:
            up.click()
        if not _set_file(page, TT["cover_file"], path, 8):
            raise LookupError("không thấy ô chọn ảnh bìa")
        page.wait_for_timeout(1500)                # TikTok mở khung cắt 3:4 – giữ nguyên, bấm Xác nhận
        ok = _button(page, TT["cover_ok"], 10)
        if ok is None:
            raise LookupError("không thấy nút Xác nhận")
        ok.click()
        return True
    except Exception:  # noqa: BLE001
        try:
            page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass
        return False


def _text_target(page, names: list[str], timeout: float):
    """Nút hoặc chữ bấm được khớp 1 trong các mẫu (TikTok hay dùng div thay cho nút)."""
    loc = _button(page, names, min(timeout, 3))
    if loc is not None:
        return loc
    end = time.time() + max(0.0, timeout - 3)
    while True:
        for n in names:
            try:
                cand = page.get_by_text(re.compile(n, re.I)).first
                if cand.count() and cand.is_visible():
                    return cand
            except Exception:  # noqa: BLE001
                continue
        if time.time() >= end:
            return None
        page.wait_for_timeout(300)


def _set_file(page, sels, path, timeout: float = 8) -> bool:
    loc = _first(page, sels, timeout, attached=True)
    if loc is None:
        return False
    loc.set_input_files(str(path))
    return True


def _click(page, sels, timeout: float = 10, enabled: bool = False) -> bool:
    loc = _first(page, sels, timeout)
    if loc is None:
        return False
    if enabled:                                  # nút "Tiếp" bị khoá lúc đang kiểm tra -> chờ
        end = time.time() + 30
        while (loc.get_attribute("aria-disabled") == "true" or loc.is_disabled()) and time.time() < end:
            page.wait_for_timeout(500)
    loc.click()
    return True


def _attach(page, sel: dict, path) -> bool:
    """Gắn file: ô chọn file có sẵn trên trang, không thì bấm nút "Thêm video…" và đưa file vào hộp chọn file."""
    loc = _first(page, sel["file"], 8, attached=True)
    if loc is not None:
        loc.set_input_files(str(path))
        return True
    btn = _button(page, sel["add"], 20)
    if btn is None:
        return False
    with page.expect_file_chooser(timeout=8000) as fc:
        btn.click()
    fc.value.set_files(str(path))
    return True


def _write_box(page, sels, text: str, wait: float) -> bool:
    loc = _first(page, sels, wait)
    if loc is None:
        return False
    _type(page, loc, text)
    return True


def _when_text(v) -> str:
    from .publish import parse_when

    dt = parse_when(v)
    return dt.strftime("%d/%m/%Y %H:%M") if dt else str(v)


FILLERS = {"youtube": _fill_youtube, "facebook": _fill_facebook, "tiktok": _fill_tiktok}


def _do_fill(eng: _Engine, settings: dict, pid: str, item: dict) -> tuple[str, dict]:
    for attempt in (0, 1):                       # bạn đã đóng cửa sổ trước đó -> mở lại
        ctx = eng.context(settings, pid)
        try:
            blank = [p for p in ctx.pages if p.url in ("about:blank", "chrome://newtab/", "")]
            page = blank[0] if blank else ctx.new_page()
            page.bring_to_front()
            break
        except Exception:  # noqa: BLE001
            eng.ctx.pop(pid, None)
            if attempt:
                raise
    rep = _Report()
    try:
        FILLERS[item["platform"]](page, item, rep)
    except Exception as e:  # noqa: BLE001
        rep.miss.append(f"(dừng giữa chừng: {type(e).__name__})")
    left = _thumb_left(item, rep)
    if left:
        reveal(Path(item["thumbnail"]))
        rep.note = "; ".join(x for x in (rep.note, left + " (đã mở thư mục chứa ảnh)") if x)
    tok = uuid.uuid4().hex[:8]
    eng.pages[tok] = (page, item["platform"], rep)
    return tok, {"done": rep.done, "miss": rep.miss, "url": rep.url, "need_login": rep.need_login, "note": rep.note}


def _state(eng: _Engine, tok: str) -> dict:
    page, platform, rep = eng.pages[tok]
    st = {"closed": False, "published": False, "url": rep.url}
    try:
        page.wait_for_timeout(30)                # cho Playwright nhận sự kiện (tab bị đóng…)
    except Exception:  # noqa: BLE001
        st["closed"] = True
    if page.is_closed():
        st["closed"] = True
    if st["closed"]:
        eng.pages.pop(tok, None)
        return st
    try:
        if platform == "youtube":
            dlg = _first(page, YT["published"], 0)
            if dlg is not None:
                st["published"] = True
                a = dlg.locator('a[href*="youtu"]').first
                if a.count():
                    st["url"] = a.get_attribute("href") or a.inner_text() or st["url"]
        elif platform == "tiktok" and "/tiktokstudio/content" in page.url:
            st["published"] = True
    except Exception:  # noqa: BLE001
        pass
    if st["published"]:
        eng.pages.pop(tok, None)
    return st


def summary(rep: dict) -> str:
    parts = []
    if rep["done"]:
        parts.append("đã điền: " + ", ".join(rep["done"]))
    if rep["miss"]:
        parts.append("điền tay: " + ", ".join(rep["miss"]))
    if rep.get("note"):
        parts.append(rep["note"])
    return " · ".join(parts)


def fill_and_wait(settings: dict, pid: str, item: dict, progress=None, poll: float = 1.5) -> dict:
    """Mở tab, điền sẵn, rồi chờ bạn bấm Đăng (YouTube / TikTok nhận ra là đã đăng) hoặc đóng tab. Huỷ = thôi chờ (tab
    vẫn để nguyên). Trả kết quả cho `publish.publish`."""
    why = problem(settings)
    if why:
        raise PublishError(why)
    name = {"youtube": "YouTube", "facebook": "Facebook", "tiktok": "TikTok"}[item["platform"]]
    if progress:
        progress(0.05, f"Mở Chrome, điền sẵn bài đăng {name}…")
    eng = engine()
    tok, rep = eng.call(_do_fill, settings, pid, item)
    if rep["need_login"]:
        raise PublishError(f"Hồ sơ Chrome này chưa đăng nhập {name} – đóng cửa sổ Chrome của tool, vào Cài đặt → Đăng "
                           f"video → Chrome (điền sẵn) bấm 'Đăng nhập {name}', đăng nhập xong đóng cửa sổ đó rồi bấm "
                           "Đăng lại.")
    text = summary(rep)
    msg = f"Đã điền sẵn trên Chrome – kiểm tra rồi bấm Đăng (nút viền cam), xong thì đóng tab. {text}"
    while True:
        if progress:
            progress(0.9, msg)
        net.sleep(poll)
        st = eng.call(_state, tok)
        if st["closed"] or st["published"]:
            break
    warn = "; ".join(x for x in (("điền tay: " + ", ".join(rep["miss"])) if rep["miss"] else "", rep.get("note"))
                     if x)
    return {"remote_id": "", "url": st.get("url") or rep.get("url", ""),
            "status": "published" if st["published"] else "browser", "warn": warn}
