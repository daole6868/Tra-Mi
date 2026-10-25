"""Chế độ VỪA TÁCH VỪA DỊCH của tab Tự động: nghe tới đâu dịch tới đó, dịch tới đâu đọc giọng tới đó.

  luồng nhận dạng (tác vụ chính)  --câu nháp-->  luồng dịch  --câu đã dịch-->  luồng đọc giọng (Edge)
  nghe xong mỗi nhóm khúc (~3 phút)              đủ "Số câu mỗi lần gửi"        đọc trước vào kho đệm work/tts
                                                 của công cụ dịch thì gửi

Câu nháp là câu tách từ các khúc vừa nghe xong; sau khi nghe xong cả phim, bước nghe lại đoạn sót / căn thời gian /
nối câu / phân biệt người nói có thể đổi vài câu. Câu cuối cùng trùng chữ với câu nháp -> dùng lại bản dịch (và file
giọng đã đọc: cùng lời + cùng giọng = cùng file); câu khác được dịch / đọc bình thường ở bước sau. Không câu nào bị
bỏ sót, chỉ tốn thêm vài lần gọi API cho các câu bị đổi.

Luồng dịch và luồng đọc giọng không đụng vào file dự án (chỉ tác vụ chính ghi) – bảng câu nháp ghi riêng
work/live_draft.json để tab Phụ đề hiện ngay trong lúc chạy.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from . import net
from .ffmpeg import Cancelled
from .subtitle import Segment

DRAFT_FILE = "live_draft.json"
TTS_GROUP = 40       # đọc trước theo từng nhóm ≤ 40 câu đã dịch
TTS_WAIT = 3.0       # hoặc sau 3 s không có câu mới


def draft_path(work: str | Path) -> Path:
    return Path(work) / DRAFT_FILE


def read_draft(work: str | Path) -> list[Segment] | None:
    """Câu nháp đang chạy (cho tab Phụ đề). None = không có / đang ghi dở."""
    f = draft_path(work)
    try:
        d = json.loads(f.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return [Segment.from_dict(x) for x in d.get("lines") or []]


class LivePipeline:
    """Dịch + đọc trước song song với lúc đang nghe. `feed(câu nháp)` gọi từ luồng nhận dạng."""

    def __init__(self, p, settings: dict, engine: str, style: str | None, target: str,
                 cancel: Callable[[], bool] | None = None, dub: dict | None = None):
        from . import pipeline, skills
        from .translate import LOCAL_ENGINES, Translator, parse_glossary

        self.work = p.work
        self.file = draft_path(p.work)
        self.cancel_outer = cancel
        self.stopped = False
        self.lock = threading.Condition()
        self.wlock = threading.Lock()
        self.lines: list[Segment] = []          # mọi câu nháp theo thứ tự (hiện ở tab Phụ đề)
        self.pending: list[Segment] = []        # câu nháp chưa gửi dịch
        self.trans: dict[str, str] = {}         # câu gốc -> bản dịch
        self.to_read: list[Segment] = []        # câu đã dịch chờ đọc trước
        self.read_n = 0
        self.closed = False
        self.error = ""
        self.net_msg = ""
        self.engine = engine
        is_ai = engine not in LOCAL_ENGINES
        self.tr = Translator(settings, engine, style, parse_glossary(p.data.get(pipeline.glossary_key(target), "")),
                             dict(p.data.get("cache") or {}), target=target, source=pipeline.source_lang(p),
                             skill=skills.skill_text(settings) if is_ai else "")
        # đủ "Số câu mỗi lần gửi" của công cụ dịch đang chọn (Cài đặt → Công cụ dịch) thì gửi
        self.batch = max(1, int(self.tr.batch))
        self.summary = p.data.get("film_profile", "") or p.data.get("summary", "")
        self.dub = dub   # {"voice", "rate", "settings", "lang", "drop_interj"} hoặc None = không đọc trước
        self.threads = [threading.Thread(target=self._translate_loop, daemon=True, name="trami-live-tr")]
        if dub:
            self.threads.append(threading.Thread(target=self._dub_loop, daemon=True, name="trami-live-tts"))
        self._write()
        for t in self.threads:
            t.start()

    # ------------------------------------------------------------ chung
    def _cancelled(self) -> bool:
        return self.stopped or bool(self.cancel_outer and self.cancel_outer())

    def _report(self, m: str):
        self.net_msg = m if "Mất mạng" in m else ""

    def status(self) -> str:
        """Thêm vào dòng tiến độ của tác vụ chính."""
        with self.lock:
            n, d = len(self.lines), sum(1 for x in self.lines if x.text in self.trans)
        parts = [f"đã dịch {d}/{n} câu"] if n else []
        if self.dub and self.read_n:
            parts.append(f"đọc trước {self.read_n} câu")
        if self.net_msg:
            parts.append(self.net_msg)
        elif self.error:
            parts.append("dịch song song tạm dừng: " + self.error[:80])
        return ("  ·  " + " · ".join(parts)) if parts else ""

    def _write(self):
        with self.lock:
            rows = [dict(x.to_dict(), trans=self.trans.get(x.text, "")) for x in self.lines]
        with self.wlock:   # luồng nghe + luồng dịch cùng ghi -> lần lượt (không ghi chồng file tạm)
            tmp = self.file.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps({"t": time.time(), "lines": rows}, ensure_ascii=False), "utf-8")
                tmp.replace(self.file)
            except OSError:   # Windows: tab Phụ đề đang đọc file -> lần sau ghi
                pass

    # ------------------------------------------------------------ nhận câu nháp
    def feed(self, drafts: list[Segment]):
        with self.lock:
            if self.closed:
                return
            for s in drafts:
                x = Segment(s.start, s.end, s.text, "", s.speaker, {})
                self.lines.append(x)
                if x.text not in self.trans:
                    self.pending.append(x)
            self.lock.notify_all()
        self._write()

    # ------------------------------------------------------------ dịch
    def _translate_loop(self):
        with net.task(self._cancelled, self._report):
            while True:
                with self.lock:
                    while not self.closed and len(self.pending) < self.batch:
                        self.lock.wait(0.5)
                        if self._cancelled():
                            return
                    if self.closed or self._cancelled():
                        return
                    part, self.pending = self.pending[:self.batch], self.pending[self.batch:]
                part = [x for x in part if x.text not in self.trans]
                if not part:
                    continue
                todo = [Segment(x.start, x.end, x.text) for x in part]
                try:
                    self.tr.translate_segments(todo, None, self._cancelled, auto_terms=False, summary=self.summary)
                except Cancelled:
                    return
                except Exception as e:  # noqa: BLE001 – lỗi (key, hết lượt…): bước dịch sau khi nghe xong báo lỗi rõ
                    from .config import err_text

                    self.error = err_text(e)
                    return
                with self.lock:
                    for x in todo:
                        if x.trans.strip():
                            self.trans[x.text] = x.trans
                            if self.dub:
                                self.to_read.append(Segment(x.start, x.end, x.text, x.trans))
                    self.lock.notify_all()
                self._write()

    # ------------------------------------------------------------ đọc trước giọng (Edge)
    def _dub_loop(self):
        from .tts import synthesize_dub
        from .voicefix import lone_interjection

        d = self.dub
        with net.task(self._cancelled, self._report):
            while True:
                with self.lock:
                    t0 = time.time()
                    while not self.closed and len(self.to_read) < TTS_GROUP and \
                            not (self.to_read and time.time() - t0 > TTS_WAIT):
                        self.lock.wait(0.5)
                        if self._cancelled():
                            return
                    if self.closed or self._cancelled():
                        return
                    part, self.to_read = self.to_read[:TTS_GROUP], self.to_read[TTS_GROUP:]
                if d.get("drop_interj"):   # câu chỉ có "A!", "Ừm." sẽ bị xoá trước khi lồng -> không đọc
                    part = [x for x in part if not lone_interjection(x.trans)]
                st0 = d["settings"]
                if st0.get("split_long_trans", True):   # câu dài sẽ bị tách thành nhiều vế sau khi dịch -> không đọc cả câu
                    from .align import split_clauses

                    mu = float(st0.get("split_long_units", 14) or 14)
                    part = [x for x in part if len(split_clauses(x, d["lang"], mu)) == 1]
                if not part:
                    continue
                st: dict = {}
                try:
                    synthesize_dub(part, self.work / "live_tts.wav", 0, self.work / "tts", d["voice"], d["rate"],
                                   cancel=self._cancelled, voices={}, settings=d["settings"], stats=st,
                                   lang=d["lang"], prepare_only=True)
                except Cancelled:
                    return
                except Exception:  # noqa: BLE001 – đọc trước hỏng thì bước lồng tiếng đọc lại bình thường
                    continue
                self.read_n += int(st.get("prepared", 0))

    # ------------------------------------------------------------ kết thúc
    def finish(self) -> dict[str, str]:
        """Nghe xong cả phim: dừng nhận câu mới, đợi lần gửi dịch / đọc đang chạy xong (câu nháp chưa gửi để bước dịch
        chính làm – nó dịch theo đúng câu cuối cùng). Trả về {câu gốc: bản dịch}."""
        with self.lock:
            self.closed = True
            self.pending = []
            self.lock.notify_all()
        for t in self.threads:
            while t.is_alive():
                t.join(0.2)
                if self.cancel_outer and self.cancel_outer():
                    self.stop()
                    raise Cancelled()
        return dict(self.trans)

    def stop(self):
        self.stopped = True
        with self.lock:
            self.closed = True
            self.lock.notify_all()

    def cleanup(self):
        try:
            self.file.unlink()
        except OSError:
            pass
