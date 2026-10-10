"""Dịch phụ đề Trung -> Việt.

Hỗ trợ:
  - google    : miễn phí, không cần key (deep-translator)
  - openai    : OpenAI hoặc bất kỳ API tương thích OpenAI (base_url tuỳ chỉnh)
  - anthropic : Claude
  - gemini    : Google Gemini (qua endpoint tương thích OpenAI)
  - deepseek  : DeepSeek (rất giỏi tiếng Trung, giá rẻ)
  - ollama    : chạy mô hình offline trên máy (VD qwen2.5)

Với các engine AI: dịch theo lô có ngữ cảnh, tự trích tên riêng -> Hán Việt để thống nhất,
bắt buộc trả về JSON đúng số dòng, tự thử lại và chia nhỏ lô khi lỗi.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

import requests

from .ffmpeg import Cancelled
from .subtitle import Segment

ProgressCB = Callable[[float, str], None]

STYLES = {
    "auto": "Tự nhận biết thể loại từ nội dung và dịch cho phù hợp.",
    "co_trang": ("Phim cổ trang/tiên hiệp/kiếm hiệp: dùng văn phong cổ trang Việt Nam, xưng hô ta - ngươi, "
                 "huynh - đệ, tỷ - muội, sư phụ - đồ nhi, bổn vương, thần, trẫm, nô tỳ...; tên người, địa danh, "
                 "môn phái, chiêu thức dịch sang Hán Việt."),
    "hien_dai": ("Phim hiện đại/ngôn tình/đô thị: văn phong tự nhiên đời thường, xưng hô anh - em, tôi - cậu, "
                 "chị - em, bố - con... theo quan hệ nhân vật; tên người dịch Hán Việt."),
    "review": ("Video review/tóm tắt phim/kể chuyện: văn phong kể chuyện lôi cuốn, mạch lạc, dùng ngôi thứ ba, "
               "câu ngắn gọn dễ đọc; tên nhân vật dịch Hán Việt."),
    "hoat_hinh": "Phim hoạt hình/donghua: văn phong sinh động, dễ hiểu; tên riêng dịch Hán Việt.",
    "tai_lieu": "Phóng sự/tài liệu/hướng dẫn/bán hàng: văn phong trang trọng, chính xác thuật ngữ, rõ ràng.",
}

SYSTEM_PROMPT = """Bạn là chuyên gia biên dịch phụ đề phim từ tiếng Trung sang tiếng Việt với hơn 15 năm kinh nghiệm.
Yêu cầu bắt buộc:
1. Dịch sát nghĩa, tự nhiên, trôi chảy như người Việt nói; KHÔNG dịch word-by-word, KHÔNG bỏ sót ý.
2. Hiểu đúng thành ngữ, tiếng lóng, từ mạng Trung Quốc và chuyển sang cách diễn đạt tương đương tiếng Việt.
3. Chọn đại từ xưng hô đúng theo quan hệ, tuổi tác, địa vị nhân vật và giữ nhất quán trong toàn bộ phim.
4. Tên người, địa danh, tổ chức, chiêu thức: phiên âm Hán Việt (VD: 李逍遥 -> Lý Tiêu Dao, 北京 -> Bắc Kinh). Tuân thủ tuyệt đối BẢNG THUẬT NGỮ nếu có.
5. Câu phụ đề ngắn gọn, dễ đọc nhanh; mỗi dòng đầu vào tương ứng đúng MỘT dòng đầu ra, không gộp, không tách, giữ nguyên số thứ tự.
6. Dòng gốc có thể bị nhận dạng sai vài chữ đồng âm: dựa vào ngữ cảnh để hiểu và dịch đúng ý.
7. Chỉ trả về JSON hợp lệ dạng {"1": "bản dịch", "2": "bản dịch", ...} không kèm giải thích.
8. Dòng có ký hiệu ⟪≤N tiếng⟫ ở cuối là giới hạn độ dài để LỒNG TIẾNG khớp thời gian nhân vật nói: bản dịch KHÔNG được
   vượt N tiếng (âm tiết). Ưu tiên cách nói gọn, tự nhiên; lược từ đệm, giữ đủ ý chính. KHÔNG chép ký hiệu ⟪⟫ vào bản dịch."""

SYL_PER_SEC = 4.6      # tiếng Việt lồng tiếng tự nhiên ~4–5 tiếng/giây
BUDGET_RE = re.compile(r"\s*⟪[^⟫]*⟫\s*")

SHORTEN_PROMPT = """Bạn là biên tập viên lồng tiếng phim. Mỗi mục gồm câu gốc tiếng Trung, bản dịch tiếng Việt hiện tại và
số tiếng (âm tiết) tối đa. Hãy viết lại bản dịch NGẮN HƠN, không vượt số tiếng tối đa, vẫn tự nhiên, giữ đúng ý chính,
giữ nguyên xưng hô và tên riêng. Chỉ trả về JSON {"1": "câu mới", ...} không kèm giải thích."""


def syllables(text: str) -> int:
    return len([w for w in re.split(r"[\s,.;:!?…\-–—\"'()]+", text or "") if w])


def budget_for(duration: float, speedup: float = 1.0) -> int:
    """Số tiếng tối đa nói vừa trong `duration` giây (cho phép tăng tốc nhẹ `speedup`)."""
    return max(2, int(duration * SYL_PER_SEC * speedup))


def parse_glossary(text: str) -> dict[str, str]:
    g: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in ("=", "\t", "=>", "->", ":"):
            if sep in line:
                a, b = line.split(sep, 1)
                if a.strip() and b.strip():
                    g[a.strip()] = b.strip().lstrip(">").strip()
                break
    return g


def glossary_text(g: dict[str, str]) -> str:
    return "\n".join(f"{k} = {v}" for k, v in g.items())


def _extract_json(txt: str) -> dict:
    txt = txt.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S)
    try:
        return json.loads(txt)
    except Exception:
        pass
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    # dự phòng: "1": "..." từng dòng
    out = {}
    for m in re.finditer(r'"(\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"', txt):
        out[m.group(1)] = json.loads('"' + m.group(2) + '"')
    if out:
        return out
    raise ValueError("Phản hồi không phải JSON hợp lệ")


# ======================================================================= LLM clients

class ApiError(RuntimeError):
    """Không gọi được API AI (mất mạng, sai URL, hết tiền, quá tải…) – phải báo cho người dùng, không tự đổi engine."""

class LLMClient:
    def __init__(self, settings: dict, engine: str):
        self.engine = engine
        self.s = settings
        if engine == "anthropic":
            self.api_key = settings.get("anthropic_api_key", "")
            self.model = settings.get("anthropic_model") or "claude-opus-5-5"
        elif engine == "gemini":
            self.api_key = settings.get("gemini_api_key", "")
            self.model = settings.get("gemini_model") or "gemini-2.5-flash"
            self.base = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif engine == "deepseek":
            self.api_key = settings.get("deepseek_api_key", "")
            self.model = settings.get("deepseek_model") or "deepseek-chat"
            self.base = "https://api.deepseek.com/v1"
        elif engine == "ollama":
            self.api_key = "ollama"
            self.model = settings.get("ollama_model") or "qwen2.5:7b"
            self.base = (settings.get("ollama_base_url") or "http://localhost:11434/v1").rstrip("/")
        else:
            self.api_key = settings.get("openai_api_key", "")
            self.model = settings.get("openai_model") or "gpt-4o-mini"
            self.base = (settings.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        if not self.api_key:
            raise ValueError(f"Chưa nhập API key cho engine '{engine}'. Vào tab Cài đặt để nhập.")
        self._json_mode = True
        # Các model Claude đời mới (và một số model OpenAI) không nhận tham số temperature -> không gửi
        self._send_temp = engine != "anthropic"

    def chat(self, system: str, user: str, max_tokens: int = 8000, temperature: float = 0.3) -> str:
        last_err = None
        for attempt in range(4):
            try:
                if self.engine == "anthropic":
                    body = {"model": self.model, "max_tokens": max(max_tokens, 16000),  # chừa chỗ cho phần suy nghĩ
                            "system": system, "messages": [{"role": "user", "content": user}]}
                    if self._send_temp:
                        body["temperature"] = temperature
                    r = requests.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                 "content-type": "application/json"},
                        json=body, timeout=300)
                    if r.status_code == 200:
                        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
                else:
                    body = {"model": self.model, "max_tokens": max_tokens,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                    if self._send_temp:
                        body["temperature"] = temperature
                    if self._json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = requests.post(f"{self.base}/chat/completions",
                                      headers={"Authorization": f"Bearer {self.api_key}"}, json=body, timeout=300)
                    if r.status_code == 400 and self._json_mode and "response_format" in r.text:
                        self._json_mode = False
                        continue
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"] or ""
                if r.status_code == 400 and self._send_temp and "temperature" in r.text:
                    self._send_temp = False  # model không hỗ trợ temperature -> gửi lại không có nó
                    continue
                if r.status_code in (401, 403):
                    raise PermissionError(f"API key không hợp lệ hoặc không có quyền ({r.status_code}): {r.text[:300]}")
                if r.status_code == 404:
                    raise ValueError(f"Không tìm thấy model '{self.model}' ({r.text[:300]})")
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            except (PermissionError, ValueError):
                raise
            except requests.RequestException as e:
                last_err = e
            if attempt < 3:
                time.sleep(2 * 2 ** attempt)
        raise ApiError(f"Không gọi được API dịch {self.engine} ({self.model}): {str(last_err)[:250]}. "
                       f"Kiểm tra Internet / API key / số dư tài khoản ở trang Cài đặt.")


# ======================================================================= Translator

class Translator:
    def __init__(self, settings: dict, engine: str | None = None, style: str | None = None,
                 glossary: dict[str, str] | None = None, cache: dict | None = None):
        self.s = settings
        self.engine = engine or settings.get("translate_engine", "google")
        self.style = style or settings.get("translate_style", "auto")
        self.glossary = dict(parse_glossary(settings.get("glossary", "")))
        if glossary:
            self.glossary.update(glossary)
        self.cache = cache if cache is not None else {}
        self.batch = max(5, int(settings.get("translate_batch", 40)))
        self.llm = None if self.engine == "google" else LLMClient(settings, self.engine)
        self.fit_dub = bool(settings.get("dub_fit", True))   # dịch vừa thời lượng để lồng tiếng khớp
        self.fallback_lines = 0   # số câu AI trả lỗi định dạng, phải dùng Google bù

    # ------------------------------------------------------------- public
    def translate_segments(self, segs: list[Segment], progress: ProgressCB | None = None,
                           cancel: Callable[[], bool] | None = None, only_empty: bool = False,
                           auto_terms: bool = True, summary: str = "") -> list[Segment]:
        idx = [i for i, s in enumerate(segs) if s.text.strip() and (not only_empty or not s.trans.strip())]
        if not idx:
            return segs
        if self.llm and auto_terms:
            if progress:
                progress(0.01, "AI đang đọc toàn bộ kịch bản để thống nhất tên riêng, xưng hô…")
            try:
                summary = summary or self.analyze([segs[i].text for i in idx])
            except Exception:
                summary = summary or ""
        texts = [segs[i].text for i in idx]
        if self.llm and self.fit_dub:
            # dịch vừa thời lượng: mỗi câu kèm giới hạn số tiếng theo thời gian nhân vật nói
            texts = [f"{segs[i].text} ⟪≤{budget_for(segs[i].duration, 1.1)} tiếng⟫" for i in idx]
        results = self.translate_lines(texts, progress, cancel, summary=summary)
        for i, t in zip(idx, results):
            segs[i].trans = BUDGET_RE.sub(" ", t or "").strip()
            if segs[i].trans:   # nhớ cả theo câu gốc -> dịch lại / dịch câu chọn không tốn API
                self.cache.setdefault(f"{self.engine}|{self.style}|{segs[i].text}", segs[i].trans)
        return segs

    def shorten(self, items: list[tuple[str, str, int]]) -> list[str]:
        """Rút gọn bản dịch quá dài so với thời gian nói. items: (câu gốc, bản dịch, số tiếng tối đa)."""
        if not self.llm or not items:
            return [vi for _, vi, _ in items]
        payload = {str(k + 1): {"goc": zh, "dich": vi, "toi_da": f"{n} tiếng"} for k, (zh, vi, n) in enumerate(items)}
        user = "RÚT GỌN CÁC BẢN DỊCH SAU:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        try:
            d = _extract_json(self.llm.chat(SHORTEN_PROMPT, user))
        except Exception:  # noqa: BLE001
            return [vi for _, vi, _ in items]
        out = []
        for k, (_, vi, n) in enumerate(items):
            v = d.get(str(k + 1)) if isinstance(d, dict) else None
            v = BUDGET_RE.sub(" ", v).strip() if isinstance(v, str) else ""
            out.append(v if v and syllables(v) < syllables(vi) else vi)
        return out

    def translate_lines(self, lines: list[str], progress: ProgressCB | None = None,
                        cancel: Callable[[], bool] | None = None, summary: str = "") -> list[str]:
        out: list[str | None] = [None] * len(lines)
        todo = []
        for i, l in enumerate(lines):
            key = f"{self.engine}|{self.style}|{l}"
            if key in self.cache:
                out[i] = self.cache[key]
            else:
                todo.append(i)
        done = len(lines) - len(todo)
        if self.engine == "google":
            batches = self._google_batches([lines[i] for i in todo])
            pos = 0
            for chunk in batches:
                if cancel and cancel():
                    raise Cancelled()
                res = self._google(chunk)
                for j, r in enumerate(res):
                    gi = todo[pos + j]
                    out[gi] = r
                    self.cache[f"{self.engine}|{self.style}|{lines[gi]}"] = r
                pos += len(chunk)
                done += len(chunk)
                if progress:
                    progress(done / len(lines), f"Đang dịch {done}/{len(lines)} câu (Google)")
        else:
            for b in range(0, len(todo), self.batch):
                if cancel and cancel():
                    raise Cancelled()
                ids = todo[b:b + self.batch]
                ctx_before = [(lines[k], out[k] or "") for k in range(max(0, ids[0] - 6), ids[0])]
                ctx_after = [lines[k] for k in range(ids[-1] + 1, min(len(lines), ids[-1] + 4))]
                res = self._llm_batch([lines[i] for i in ids], ctx_before, ctx_after, summary)
                for gi, r in zip(ids, res):
                    out[gi] = r
                    self.cache[f"{self.engine}|{self.style}|{lines[gi]}"] = r
                done += len(ids)
                if progress:
                    progress(done / len(lines), f"AI đang dịch {done}/{len(lines)} câu ({self.engine})")
        return [self._post(o or "") for o in out]

    # ------------------------------------------------------------- AI
    def analyze(self, texts: list[str]) -> str:
        """Đọc kịch bản, trích tên riêng + thuật ngữ -> Hán Việt, đoán thể loại & xưng hô. Cập nhật glossary."""
        assert self.llm
        full = "\n".join(texts)
        chunks, cur = [], ""
        for line in full.split("\n"):
            if len(cur) + len(line) > 7000:
                chunks.append(cur)
                cur = ""
            cur += line + "\n"
        if cur:
            chunks.append(cur)
        notes = []
        for ch in chunks[:6]:  # đọc tối đa ~40k ký tự đầu là đủ nắm nhân vật
            prompt = (
                "Đọc đoạn kịch bản phụ đề tiếng Trung dưới đây và trả về JSON với các khoá:\n"
                '"genre": thể loại phim (cổ trang/tiên hiệp/hiện đại/review/...)\n'
                '"terms": object ánh xạ TÊN RIÊNG tiếng Trung (tên người, biệt danh, địa danh, môn phái, tổ chức, '
                "chiêu thức, bảo vật) -> phiên âm Hán Việt chuẩn. Chỉ lấy tên riêng thực sự, không lấy từ thông thường.\n"
                '"relations": mô tả ngắn quan hệ giữa các nhân vật chính và cách xưng hô tiếng Việt phù hợp.\n\n'
                "KỊCH BẢN:\n" + ch)
            try:
                d = _extract_json(self.llm.chat("Bạn là chuyên gia Hán Việt và biên dịch phim Trung Quốc. Chỉ trả JSON.",
                                                prompt, max_tokens=4000, temperature=0.1))
            except Exception:
                continue
            for k, v in (d.get("terms") or {}).items():
                if isinstance(v, str) and k and v and k not in self.glossary and len(k) <= 12:
                    self.glossary[k] = v
            g, r = d.get("genre"), d.get("relations")
            if g or r:
                notes.append(f"Thể loại: {g}. Quan hệ/xưng hô: {r}")
        return "\n".join(notes[:2])

    def _llm_batch(self, lines: list[str], ctx_before, ctx_after, summary: str, depth: int = 0) -> list[str]:
        gl = {k: v for k, v in self.glossary.items() if any(k in l for l in lines)}
        parts = [f"PHONG CÁCH: {STYLES.get(self.style, STYLES['auto'])}"]
        if summary:
            parts.append(f"THÔNG TIN PHIM:\n{summary}")
        if gl:
            parts.append("BẢNG THUẬT NGỮ (bắt buộc dùng):\n" + glossary_text(gl))
        if ctx_before:
            parts.append("NGỮ CẢNH TRƯỚC (đã dịch, chỉ để tham khảo, KHÔNG dịch lại):\n" +
                         "\n".join(f"- {a} => {b}" for a, b in ctx_before))
        if ctx_after:
            parts.append("NGỮ CẢNH SAU (chỉ tham khảo):\n" + "\n".join(f"- {a}" for a in ctx_after))
        payload = {str(i + 1): l for i, l in enumerate(lines)}
        parts.append(f"DỊCH {len(lines)} DÒNG SAU sang tiếng Việt, trả về JSON với đúng các khoá 1..{len(lines)}:\n"
                     + json.dumps(payload, ensure_ascii=False, indent=0))
        user = "\n\n".join(parts)
        try:
            d = _extract_json(self.llm.chat(SYSTEM_PROMPT, user))
        except (PermissionError, ApiError, ValueError) as e:
            if isinstance(e, ValueError) and "JSON" in str(e):
                d = None  # AI trả về sai định dạng -> chia nhỏ lô rồi thử lại
            else:
                raise     # lỗi kết nối / key / model: báo rõ, KHÔNG tự đổi sang Google
        except Exception:
            d = None
        if d is not None:
            res = [(d.get(str(i + 1)) or "").strip() if isinstance(d.get(str(i + 1)), str) else "" for i in range(len(lines))]
            miss = [i for i, r in enumerate(res) if not r and lines[i].strip()]
            if not miss:
                return res
            if len(miss) <= max(2, len(lines) // 8) and depth < 4:
                fill = self._llm_batch([lines[i] for i in miss], ctx_before, ctx_after, summary, depth + 1)
                for i, f in zip(miss, fill):
                    res[i] = f
                return res
        # lỗi hoặc thiếu nhiều dòng -> chia đôi lô rồi thử lại
        if len(lines) > 1 and depth < 4:
            mid = len(lines) // 2
            a = self._llm_batch(lines[:mid], ctx_before, [], summary, depth + 1)
            b = self._llm_batch(lines[mid:], [], ctx_after, summary, depth + 1)
            return a + b
        # cuối cùng dùng Google để không bị trống (đếm lại để cảnh báo người dùng)
        try:
            self.fallback_lines += len(lines)
            return self._google(lines)
        except Exception:
            return [""] * len(lines)

    # ------------------------------------------------------------- Google
    def _apply_glossary_src(self, text: str) -> str:
        for k in sorted(self.glossary, key=len, reverse=True):
            if k in text:
                text = text.replace(k, f" {self.glossary[k]} ")
        return text

    def _google_batches(self, lines: list[str], limit: int = 4000) -> list[list[str]]:
        batches, cur, n = [], [], 0
        for l in lines:
            if cur and (n + len(l) + 1 > limit or len(cur) >= 80):
                batches.append(cur)
                cur, n = [], 0
            cur.append(l)
            n += len(l) + 1
        if cur:
            batches.append(cur)
        return batches

    @staticmethod
    def _gtx(text: str) -> str:
        """Google Translate (endpoint miễn phí trả JSON)."""
        r = requests.post("https://translate.googleapis.com/translate_a/single",
                          params={"client": "gtx", "sl": "zh-CN", "tl": "vi", "dt": "t", "ie": "UTF-8", "oe": "UTF-8"},
                          data={"q": text}, timeout=30,
                          headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        data = r.json()
        return "".join(part[0] for part in (data[0] or []) if part and part[0])

    @staticmethod
    def _clients5(text: str) -> str:
        r = requests.get("https://clients5.google.com/translate_a/t",
                         params={"client": "dict-chrome-ex", "sl": "zh-CN", "tl": "vi", "q": text}, timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        d = r.json()
        if isinstance(d, list) and d:
            x = d[0]
            return x[0] if isinstance(x, list) else str(x)
        if isinstance(d, dict) and d.get("sentences"):
            return "".join(s.get("trans", "") for s in d["sentences"])
        raise ValueError("Phản hồi Google không hợp lệ")

    def _gt_one(self, text: str) -> str:
        errs = []
        for attempt in range(3):
            for fn in (self._gtx, self._clients5):
                try:
                    return fn(text)
                except Exception as e:
                    errs.append(f"{fn.__name__}: {e}")
            try:
                from deep_translator import GoogleTranslator

                return GoogleTranslator(source="zh-CN", target="vi").translate(text) or ""
            except Exception as e:
                errs.append(f"deep_translator: {str(e)[:150]}")
            time.sleep(2 * 2 ** attempt)
        raise RuntimeError("Không kết nối được Google Dịch (kiểm tra mạng hoặc bị giới hạn tạm thời, "
                           "hãy thử lại sau hoặc dùng engine AI): " + errs[0][:200])

    def _google(self, lines: list[str]) -> list[str]:
        src = [self._apply_glossary_src(l).replace("\n", " ").strip() for l in lines]
        try:
            parts = self._gt_one("\n".join(src)).split("\n")
            if len(parts) == len(lines):
                return [p.strip() for p in parts]
        except RuntimeError:
            raise
        # số dòng lệch -> dịch từng dòng
        return [self._gt_one(l).strip() if l else "" for l in src]

    # ------------------------------------------------------------- hậu xử lý
    def _post(self, t: str) -> str:
        t = " ".join(t.replace("\n", " ").split())
        t = re.sub(r"\s+([,.!?;:])", r"\1", t)
        t = t.strip(" \"'“”")
        # bỏ dấu chấm cuối câu (phong cách phụ đề), giữ ? ! ...
        if t.endswith(".") and not t.endswith(".."):
            t = t[:-1]
        if t:
            t = t[0].upper() + t[1:]
        return t


def translate_text(text: str, settings: dict, engine: str | None = None) -> str:
    return Translator(settings, engine).translate_lines([text])[0] if text.strip() else ""
