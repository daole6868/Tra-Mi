"""Tiện ích dùng chung cho giao diện: chạy hàm nền không đơ giao diện, danh sách lựa chọn."""
from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, Qt, QThreadPool, Signal
import shiboken6
from PySide6.QtWidgets import QAbstractScrollArea, QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar, QWidget

from .. import genres

ENGINES = [("google", "Google Dịch (miễn phí)"),
           ("hymt", "Hy-MT2 – dịch trên máy, miễn phí, không cần mạng (tải ở tab Tài liệu)"),
           ("deepseek", "DeepSeek AI (giỏi tiếng Trung, rẻ)"),
           ("openai", "OpenAI GPT / API tương thích"), ("anthropic", "Claude (Anthropic)"),
           ("gemini", "Google Gemini"), ("ollama", "Ollama (offline trên máy)")]
STYLES = genres.choices()     # thể loại phim = form dịch theo thể loại (trami/genres.py, sửa được ở hộp "Sửa form")
ASR_ENGINES = [("qwen3", "Qwen3-ASR – nghe chuẩn nhất, căn giờ từng chữ (khuyên dùng, cần GPU)"),
               ("firered", "FireRedASR2 – rất chuẩn tiếng Phổ thông + giọng địa phương, lời hát (tải ở tab Tài liệu)"),
               ("sensevoice", "SenseVoice – nhanh, nhẹ"),
               ("paraformer", "Paraformer – có dấu câu + tự phân người nói"),
               ("whisper", "Whisper large-v3 – đa ngôn ngữ")]
LANGS = [("zh", "Tiếng Trung (Phổ thông)"), ("yue", "Tiếng Quảng Đông"), ("auto", "Tự nhận diện"),
         ("en", "Tiếng Anh"), ("ja", "Tiếng Nhật"), ("ko", "Tiếng Hàn")]
DUAL_HELP = ("Nghe xong bằng bộ chính, bộ còn lại (Qwen3-ASR ↔ FireRedASR2) nghe lại đúng các câu đó rồi so chữ từng "
             "câu: câu 2 bộ nghe khác nhau -> AI hỗ trợ nghe chọn bản đúng theo ngữ cảnh; không chắc -> chữ gốc tô xanh "
             "để bạn nghe lại. Cần tải FireRedASR2 ở tab Tài liệu; lâu hơn ~1,5–2 lần")
CHUNK_HELP = ("Bộ nghe chia phim thành khúc dài tối đa bấy nhiêu giây rồi nghe cả khúc 1 lần (thấy cả câu trước / sau). "
              "Mặc định 28. Dài hơn (40–60): nhiều ngữ cảnh hơn, ít chỗ cắt khúc, nhưng chỗ có nhạc dễ sót câu / lặp "
              "lời và tốn bộ nhớ card hơn. FireRedASR2 tối đa 55")
RECHECK_HELP = ("Bộ nghe tự chấm độ chắc từng chữ. Câu không chắc (chỗ lẫn nhạc, nói nhanh, câu nằm sát mép khúc) được nghe "
                "lại trong khúc mới: câu nằm giữa, thêm vài giây trước / sau làm ngữ cảnh; đã tách giọng thì nghe thêm "
                "trên tiếng gốc. Chỉ nhận bản nghe lại khi chắc hơn, giống bản cũ và không dài thêm (không thêm chữ lạ); "
                "nghe ra khác mà chưa chắc -> chữ gốc tô xanh. Thêm ~10–30 % thời gian nghe")
OVERLAP_HELP = ("Mọi câu (không chỉ câu không chắc) được nghe lần 2 trong khúc lệch với khúc lần đầu (câu nằm giữa khúc); "
                "2 lần ra khác nhau -> lấy bản chắc hơn theo cùng luật như 'Nghe lại câu không chắc'. Chính xác nhất, "
                "CHẬM GẤP ĐÔI – dùng cho phim khó (nhiều nhạc, nói nhanh)")
LANG_CHECK_HELP = ("Khúc bộ nghe (Qwen3-ASR) không chắc -> nghe lại không ép tiếng Trung xem có phải câu tiếng Anh / Nhật… "
                   "không; ra tiếng khác mà chắc hơn thì dùng bản đó. Tắt nếu phim chỉ có tiếng Trung mà thấy câu tiếng "
                   "Anh / Nhật lạ ở đoạn nhạc (bộ nghe đoán lời bài hát thành tiếng nước ngoài)")
METHODS = [("asr", "Nhận dạng giọng nói"), ("ocr", "OCR phụ đề cứng có sẵn trên video"),
           ("hybrid", "OCR + nghe – chữ từ phụ đề in sẵn, giờ từ giọng nói (chuẩn nhất khi có phụ đề Trung)")]
SPLIT_STYLES = [("dub", "Câu trọn ý – hợp lồng tiếng (khuyên dùng)"), ("short", "Câu ngắn – hợp phụ đề đọc nhanh")]
SPLIT_HELP = ("Câu trọn ý: tin dấu câu của bộ nghe, chỉ cắt ở chỗ nghỉ rất dài (≥ 1,6 s) hoặc dấu phẩy + nghỉ ≥ 0,7 s, "
              "tự nối dòng chưa hết câu → dịch và lồng tiếng liền mạch. Câu ngắn: cắt ở mọi chỗ nghỉ ≥ 0,8 s / dấu phẩy + "
              "nghỉ ≥ 0,45 s (cách tách cũ). Cả 2 kiểu đều không cắt giữa 1 từ và tự nối câu bị cắt ngang")
OCR_SPEEDS = [("2", "Nhanh – 2 lần/giây (video dài, câu hiện lâu)"), ("4", "Chuẩn – 4 lần/giây (khuyên dùng)"),
              ("8", "Kỹ – 8 lần/giây (câu đổi rất nhanh, chậm gấp đôi)")]
HARDSUB = [("none", "Giữ nguyên"), ("inpaint", "Xoá sạch bằng AI (LaMa)"), ("blur", "Làm mờ vùng phụ đề"),
           ("box", "Che bằng dải màu"), ("delogo", "Delogo (nhanh)"),
           ("patch", "Vá nền (chép nền sát bên, nhanh)"), ("stretch", "Kéo dọc (kéo dòng sát mép phủ kín chữ)")]
WHICH = [("trans", "Chỉ tiếng Việt"), ("both", "Song ngữ Việt + Trung"), ("source", "Chỉ tiếng Trung"),
         ("none", "Không ghép (chỉ xuất SRT)")]
TTS_ENGINES = [("edge", "Microsoft Edge (giọng Việt / Anh / Thái / Nhật / Hàn, cần mạng)"),
               ("vieneu", "VieNeu-TTS (25 giọng + 6 giọng thu âm sạch + nhái giọng, offline – chỉ tiếng Việt)"),
               ("omnivoice", "OmniVoice (23 giọng chọn sẵn + nhái giọng từ file mẫu, offline, đọc được nhiều tiếng)")]
TARGETS = [("vi", "Tiếng Việt"), ("en", "Tiếng Anh (English)"), ("th", "Tiếng Thái (ภาษาไทย)"),
           ("ja", "Tiếng Nhật (日本語)"), ("ko", "Tiếng Hàn (한국어)")]


def which_choices(lang: str = "vi") -> list[tuple[str, str]]:
    """Lựa chọn phụ đề ghép vào video theo tiếng đích ("Chỉ tiếng Anh", "Song ngữ Anh + Trung"…)."""
    from ..langs import INFO, norm

    short = INFO[norm(lang)]["short"]
    return [("trans", f"Chỉ tiếng {short}"), ("both", f"Song ngữ {short} + Trung"), ("source", "Chỉ tiếng Trung"),
            ("none", "Không ghép (chỉ xuất SRT)")]


def relabel(cb: QComboBox, choices: list[tuple[str, str]]):
    """Đổi chữ các mục của ô chọn (giữ mục đang chọn) – dùng khi đổi tiếng đích."""
    for i in range(cb.count()):
        for k, name in choices:
            if cb.itemData(i) == k:
                cb.setItemText(i, name)
ASPECTS = [("original", "Giữ nguyên"), ("9:16", "9:16 dọc – TikTok / Reels / Shorts"),
           ("16:9", "16:9 ngang – YouTube / Facebook"), ("1:1", "1:1 vuông"), ("4:5", "4:5 dọc – Facebook / Instagram"),
           ("3:4", "3:4 dọc"), ("2:3", "2:3 dọc – Pinterest"), ("3:5", "3:5 dọc"), ("5:7", "5:7 dọc"),
           ("4:3", "4:3 ngang"), ("3:2", "3:2 ngang"), ("5:3", "5:3 ngang"), ("5:4", "5:4 ngang"), ("7:5", "7:5 ngang")]
ASPECT_MODES = [("blur", "Nền mờ (từ chính hình phim)"), ("pad", "Viền đen"), ("color", "Nền 1 màu"),
                ("crop", "Phóng cho đầy khung (cắt bớt)")]
FILM_POS = [("c", "Giữa"), ("up", "Lệch lên – chừa chỗ chữ ở dưới"), ("down", "Lệch xuống – chừa chỗ tiêu đề ở trên"),
            ("left", "Lệch trái"), ("right", "Lệch phải"), ("custom", "Tự chỉnh")]
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
