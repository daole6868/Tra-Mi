"""Tiện ích dùng chung cho giao diện: chạy hàm nền không đơ giao diện, danh sách lựa chọn."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, Qt, QThreadPool, Signal
import shiboken6
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar, QWidget

ENGINES = [("google", "Google Dịch (miễn phí)"), ("deepseek", "DeepSeek AI (giỏi tiếng Trung, rẻ)"),
           ("openai", "OpenAI GPT / API tương thích"), ("anthropic", "Claude (Anthropic)"),
           ("gemini", "Google Gemini"), ("ollama", "Ollama (offline trên máy)")]
STYLES = [("auto", "Tự nhận biết"), ("co_trang", "Cổ trang / tiên hiệp / kiếm hiệp"),
          ("hien_dai", "Hiện đại / ngôn tình / đô thị"), ("review", "Review / tóm tắt phim"),
          ("hoat_hinh", "Hoạt hình / donghua"), ("tai_lieu", "Tài liệu / hướng dẫn / bán hàng")]
ASR_ENGINES = [("qwen3", "Qwen3-ASR – nghe chuẩn nhất, căn giờ từng chữ (khuyên dùng, cần GPU)"),
               ("sensevoice", "SenseVoice – nhanh, nhẹ"),
               ("paraformer", "Paraformer – có dấu câu + tự phân người nói"),
               ("whisper", "Whisper large-v3 – đa ngôn ngữ")]
LANGS = [("zh", "Tiếng Trung (Phổ thông)"), ("yue", "Tiếng Quảng Đông"), ("auto", "Tự nhận diện"),
         ("en", "Tiếng Anh"), ("ja", "Tiếng Nhật"), ("ko", "Tiếng Hàn")]
METHODS = [("asr", "Nhận dạng giọng nói"), ("ocr", "OCR phụ đề cứng có sẵn trên video")]
OCR_SPEEDS = [("2", "Nhanh – 2 lần/giây (video dài, câu hiện lâu)"), ("4", "Chuẩn – 4 lần/giây (khuyên dùng)"),
              ("8", "Kỹ – 8 lần/giây (câu đổi rất nhanh, chậm gấp đôi)")]
HARDSUB = [("none", "Giữ nguyên"), ("inpaint", "Xoá sạch bằng AI (LaMa)"), ("blur", "Làm mờ vùng phụ đề"),
           ("box", "Che bằng dải màu"), ("delogo", "Delogo (nhanh)")]
WHICH = [("trans", "Chỉ tiếng Việt"), ("both", "Song ngữ Việt + Trung"), ("source", "Chỉ tiếng Trung"),
         ("none", "Không ghép (chỉ xuất SRT)")]
TTS_ENGINES = [("edge", "Microsoft Edge (2 giọng, cần mạng)"), ("vieneu", "VieNeu-TTS (25 giọng + nhái giọng, offline)"),
               ("omnivoice", "OmniVoice (nhái giọng từ file ghi âm mẫu, offline)")]
ASPECTS = [("original", "Giữ nguyên"), ("9:16", "9:16 – TikTok/Reels/Shorts"), ("16:9", "16:9 – YouTube/Facebook"),
           ("1:1", "1:1 vuông"), ("4:5", "4:5 – Facebook dọc"), ("4:3", "4:3"), ("3:4", "3:4")]
ASPECT_MODES = [("blur", "Nền mờ"), ("pad", "Viền đen"), ("crop", "Cắt cho đầy khung")]
RESOLUTIONS = [("original", "Giữ nguyên"), ("480p", "480p"), ("720p", "720p HD"), ("1080p", "1080p Full HD"),
               ("1440p", "2K"), ("2160p", "4K")]
POSITIONS = [("top-right", "Trên phải"), ("top-left", "Trên trái"), ("top", "Trên giữa"),
             ("bottom-right", "Dưới phải"), ("bottom-left", "Dưới trái"), ("center", "Giữa")]


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn, sig):
        super().__init__()
        self.fn, self.sig = fn, sig

    def run(self):
        try:
            res = self.fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            from ..config import err_text, log_error

            log_error(getattr(self.fn, "__qualname__", ""))
            try:
                self.sig.error.emit(err_text(e))
            except RuntimeError:  # cửa sổ đã đóng trong lúc việc nền đang chạy
                pass
            return
        try:
            self.sig.done.emit(res)
        except RuntimeError:  # cửa sổ đã đóng trong lúc việc nền đang chạy
            pass


_keep: set = set()


def run_async(fn: Callable, on_done: Callable | None = None, on_error: Callable | None = None):
    """Chạy fn() ở luồng nền, gọi on_done(kết quả) / on_error(lỗi) trên luồng giao diện."""
    sig = _Signals()
    _keep.add(sig)

    def finish_ok(r):
        _keep.discard(sig)
        if on_done:
            on_done(r)

    def finish_err(m):
        _keep.discard(sig)
        if on_error:
            on_error(m)

    sig.done.connect(finish_ok)
    sig.error.connect(finish_err)
    QThreadPool.globalInstance().start(_Task(fn, sig))


_WHEEL_TYPES = (QComboBox, QAbstractSpinBox, QAbstractSlider)


class WheelGuard(QObject):
    """Lăn chuột lướt qua ô chọn / ô số / thanh trượt thì KHÔNG đổi giá trị mà cuộn trang.
    - Ô chọn (combobox): lăn chuột không bao giờ đổi lựa chọn – chỉ đổi khi bấm chọn trong danh sách.
    - Ô số / thanh trượt: chỉ nhận lăn chuột ngay sau khi vừa bấm vào chính nó; bấm chỗ khác là thôi."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.armed = None   # ô số / thanh trượt vừa được bấm vào

    @staticmethod
    def _owner(obj):
        """Ô điều khiển chứa obj (vd. ô gõ số bên trong ô số)."""
        w = obj
        while w is not None and hasattr(w, "parentWidget"):
            if isinstance(w, _WHEEL_TYPES) and not isinstance(w, QScrollBar):
                return w
            w = w.parentWidget()
        return None

    def eventFilter(self, obj, ev):
        try:
            return self._filter(obj, ev)
        except Exception:  # noqa: BLE001 – bộ lọc toàn cục tuyệt đối không được làm sập giao diện
            self.armed = None
            return False

    def _filter(self, obj, ev):
        t = ev.type()
        if t == QEvent.Type.MouseButtonPress:
            if self.armed is not None and not shiboken6.isValid(self.armed):
                self.armed = None   # ô cũ đã bị xoá khỏi giao diện
            own = self._owner(obj) if hasattr(obj, "parentWidget") else None
            if own is not None:
                self.armed = None if isinstance(own, QComboBox) else own
            elif self.armed is not None and isinstance(obj, QWidget) and obj.isAncestorOf(self.armed):
                pass   # cú bấm vừa rồi được chuyển tiếp lên khung chứa ô – vẫn giữ
            else:
                self.armed = None
            return False
        if t == QEvent.Type.Wheel and isinstance(obj, _WHEEL_TYPES) and not isinstance(obj, QScrollBar):
            if obj.focusPolicy() == Qt.FocusPolicy.WheelFocus:
                obj.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # lăn chuột không tự chọn ô
            allowed = (not isinstance(obj, QComboBox)) and obj is self.armed and obj.hasFocus()
            if not allowed:
                p = obj.parentWidget()
                while p is not None and not isinstance(p, QAbstractScrollArea):
                    p = p.parentWidget()
                if p is not None:  # chuyển cho khung cuộn phía ngoài để trang vẫn cuộn
                    QCoreApplication.sendEvent(p.viewport(), ev)
                return True
        return False


def install_wheel_guard(app):
    guard = WheelGuard(app)
    app.installEventFilter(guard)
    return guard
