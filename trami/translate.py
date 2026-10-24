"""Dịch phụ đề Trung -> Việt (hoặc Anh / Thái / Nhật / Hàn – ngôn ngữ đích, xem trami/langs.py).

Hỗ trợ:
  - google    : miễn phí, không cần key (deep-translator)
  - openai    : OpenAI hoặc bất kỳ API tương thích OpenAI (base_url tuỳ chỉnh)
  - anthropic : Claude
  - gemini    : Google Gemini (qua endpoint tương thích OpenAI)
  - deepseek  : DeepSeek (rất giỏi tiếng Trung, giá rẻ)
  - ollama    : chạy mô hình offline trên máy (VD qwen2.5)
  - hymt      : Hy-MT2 (Tencent) – mô hình chuyên dịch chạy trên máy (GPU / CPU), miễn phí, không cần mạng sau khi tải

Với các engine AI: dịch theo lô có ngữ cảnh, tự trích tên riêng -> Hán Việt để thống nhất,
bắt buộc trả về JSON đúng số dòng, tự thử lại và chia nhỏ lô khi lỗi.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

import requests

from . import langs, net
from .ffmpeg import Cancelled
from .subtitle import Segment
from .tts import VieneuWorker

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
   vượt N tiếng (âm tiết). Ưu tiên cách nói gọn, tự nhiên; lược từ đệm, giữ đủ ý chính. KHÔNG chép ký hiệu ⟪⟫ vào bản dịch.
9. Dòng chắc chắn là LỜI BÀI HÁT (nhạc phim, nhân vật đang hát – câu văn vần, không phải lời thoại) thì bắt đầu bản dịch
   bằng "♪ ". Lời thoại bình thường KHÔNG thêm ♪."""

# Dịch sang Anh / Thái / Nhật / Hàn: lời nhắc tiếng Anh (luật giống bản tiếng Việt, đổi phần xưng hô / tên riêng)
STYLES_EN = {
    "auto": "Infer the genre from the content and translate accordingly.",
    "co_trang": "Ancient / xianxia / wuxia drama: period, slightly formal tone; consistent titles and honorifics.",
    "hien_dai": "Modern / romance / urban drama: natural everyday speech that fits each relationship.",
    "review": "Movie recap / storytelling video: engaging narrative tone, third person, short clear sentences.",
    "hoat_hinh": "Animation / donghua: lively, easy to understand.",
    "tai_lieu": "Documentary / tutorial / sales: formal, precise terminology, clear.",
}
_ADDRESS = {"en": "", "th": " (use ครับ / ค่ะ and pronouns that fit each character)",
            "ja": " (choose keigo or casual speech to fit each relationship)",
            "ko": " (choose 존댓말 or 반말 to fit each relationship)"}
_NAMES = {"en": "the established English name if one exists, otherwise pinyin (e.g. 李逍遥 -> Li Xiaoyao)",
          "th": "the usual Thai transliteration of the Chinese name",
          "ja": "the Japanese reading in kanji or katakana as commonly used in Japanese releases",
          "ko": "the Korean (Sino-Korean) reading in Hangul (e.g. 李逍遥 -> 이소요)"}
SRC_NAMES = {"zh": ("tiếng Trung", "Chinese"), "yue": ("tiếng Quảng Đông", "Cantonese"), "en": ("tiếng Anh", "English"),
             "ja": ("tiếng Nhật", "Japanese"), "ko": ("tiếng Hàn", "Korean"), "auto": ("tiếng gốc", "the source language")}


def system_prompt(target: str, source: str = "zh") -> str:
    """Lời nhắc hệ thống cho AI theo ngôn ngữ đích (tiếng Việt: giữ nguyên bản chuẩn có luật Hán Việt)."""
    target = langs.norm(target)
    src_vi, src_en = SRC_NAMES.get(source, SRC_NAMES["zh"])
    if target == "vi":
        return SYSTEM_PROMPT.replace("từ tiếng Trung sang", f"từ {src_vi} sang", 1)
    L = langs.INFO[target]
    return f"""You are an expert film subtitle translator with 15+ years of experience, translating {src_en} dialogue into {L['english']}.
Mandatory rules:
1. Translate faithfully and naturally, the way a native {L['english']} speaker talks; never word-by-word, never drop meaning.
2. Render idioms, slang and internet expressions with natural {L['english']} equivalents.
3. Keep forms of address consistent with the characters' relationships, age and status for the whole film{_ADDRESS[target]}.
4. Names of people, places, organizations and techniques: use {_NAMES[target]}. ALWAYS follow the GLOSSARY if given.
5. Subtitles must be short and easy to read; each input line maps to exactly ONE output line; never merge or split lines; keep the numbering.
6. Source lines may contain speech-recognition errors (homophones): use the context to understand the intended meaning.
7. Return ONLY valid JSON like {{"1": "translation", "2": "translation", ...}} with no explanation.
8. A line ending with ⟪≤N {L['unit_ai']}⟫ is a length limit so the DUBBED voice fits the character's speaking time: the
   translation must NOT exceed N {L['unit_ai']}. Prefer short natural phrasing, drop filler words, keep the core meaning.
   NEVER copy the ⟪⟫ marker into the translation.
9. A line that is clearly SONG LYRICS (theme song, a character singing – verse, not dialogue) must start with "♪ ".
   Never add ♪ to normal dialogue."""


SYL_PER_SEC = 4.6      # tiếng Việt lồng tiếng tự nhiên ~4–5 tiếng/giây (tiếng khác: langs.INFO[...]["rate"])
BUDGET_RE = re.compile(r"\s*⟪[^⟫]*⟫\s*")
HAN_RE = re.compile(r"[㐀-鿿]")

SHORTEN_PROMPT = """Bạn là biên tập viên lồng tiếng phim. Mỗi mục gồm câu gốc tiếng Trung, bản dịch tiếng Việt hiện tại và
số tiếng (âm tiết) tối đa. Hãy viết lại bản dịch NGẮN HƠN, không vượt số tiếng tối đa, vẫn tự nhiên, giữ đúng ý chính,
giữ nguyên xưng hô và tên riêng. Chỉ trả về JSON {"1": "câu mới", ...} không kèm giải thích."""


SHORTEN_PROMPT_EN = """You are a film dubbing script editor. Each item has the source line, the current {lang} translation and
the maximum length in {unit}. Rewrite the translation SHORTER, within the maximum length, still natural, keeping the core
meaning, names and forms of address. Return ONLY JSON {{"1": "new line", ...}} with no explanation."""


def syllables(text: str, lang: str = "vi") -> int:
    """Độ dài lời: số tiếng / từ (Việt, Anh), số chữ (Thái, Nhật, Hàn)."""
    return langs.units(text, lang)


def budget_for(duration: float, speedup: float = 1.0, lang: str = "vi") -> int:
    """Độ dài lời tối đa nói vừa trong `duration` giây (cho phép tăng tốc nhẹ `speedup`) – theo tiếng."""
    return langs.budget(duration, lang, speedup)


KANA_RE = re.compile(r"[\u3040-\u30FF]")


def untranslated(text: str, lang: str = "vi") -> bool:
    """Bản dịch rỗng hoặc vẫn còn chữ Hán (AI chép lại câu gốc / bỏ sót) -> coi như chưa dịch. Dịch sang tiếng Nhật thì
    chữ Hán là bình thường: chỉ coi là chưa dịch khi không có chữ kana nào mà có ≥ 3 chữ Hán (chép nguyên câu Trung)."""
    t = (text or "").strip()
    if not t:
        return True
    if langs.norm(lang) == "ja":
        return not KANA_RE.search(t) and len(HAN_RE.findall(t)) >= 3
    return len(HAN_RE.findall(t)) >= 2


def cache_line(key: str) -> str:
    """Câu gốc trong 1 khoá bộ nhớ bản dịch (khoá tiếng Việt cũ "máy|kiểu|câu", tiếng khác "máy|kiểu|tiếng|câu")."""
    parts = key.split("|", 3)
    if len(parts) == 4 and parts[2] in langs.INFO:
        return parts[3]
    return key.split("|", 2)[2] if len(parts) >= 3 else key


def with_skill(system: str, skill: str) -> str:
    from .skills import with_skill as _ws

    return _ws(system, skill)


def cache_key(engine: str, style: str, target: str, line: str) -> str:
    """Khoá bộ nhớ bản dịch (tiếng Việt giữ dạng cũ để dùng lại bản dịch đã nhớ của dự án cũ)."""
    target = langs.norm(target)
    return f"{engine}|{style}|{line}" if target == "vi" else f"{engine}|{style}|{target}|{line}"


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

AI_ENGINES = ("deepseek", "openai", "anthropic", "gemini", "ollama")
ENGINE_NAMES = {"deepseek": "DeepSeek", "openai": "OpenAI", "anthropic": "Claude", "gemini": "Gemini", "ollama": "Ollama"}
# lần gửi / phút mặc định: gói miễn phí Gemini giới hạn số lần gọi mỗi phút (xem trang Rate limits của Google)
DEFAULT_RPM = {"gemini": 8}


def api_keys(settings: dict, engine: str) -> list[str]:
    """Các API key của 1 công cụ (Cài đặt `<engine>_api_keys`, cộng key cũ `<engine>_api_key`), bỏ trùng / rỗng."""
    out: list[str] = []
    for k in [settings.get(f"{engine}_api_key", "")] + list(settings.get(f"{engine}_api_keys") or []):
        k = (k or "").strip()
        if k and not k.startswith("••••") and k not in out:
            out.append(k)
    return out


def engine_batch(settings: dict, engine: str) -> int:
    """Số câu mỗi lần gửi AI của công cụ này (khung riêng ở Cài đặt; chưa đặt -> 'Số câu mỗi lần gửi AI' cũ)."""
    return max(1, int(settings.get(f"{engine}_batch") or settings.get("translate_batch") or 40))


def engine_rpm(settings: dict, engine: str) -> int:
    """Số lần gửi tối đa mỗi phút cho MỖI key (0 = không giới hạn)."""
    v = settings.get(f"{engine}_rpm")
    return max(0, int(DEFAULT_RPM.get(engine, 0) if v is None else v))


# nhớ trong phiên: key đang dùng tốt / key hỏng / các lần gửi gần đây của mỗi key (giới hạn mỗi phút)
_KEY_POS: dict[str, int] = {}
_KEY_DEAD: dict[str, tuple[float, str]] = {}   # key -> (hết hạn chặn lúc, lý do)
_KEY_CALLS: dict[str, list[float]] = {}
DAY_BLOCK = 3600.0        # key hết lượt trong ngày: 1 giờ sau mới thử lại
WAIT_LIMIT_429 = 20       # bị giới hạn mỗi phút mà chỉ có 1 key: chờ rồi gửi lại tối đa 20 lần


def _key_tag(engine: str, i: int, n: int) -> str:
    return f"key {ENGINE_NAMES.get(engine, engine)} số {i + 1}" + (f"/{n}" if n > 1 else "")


class LLMClient:
    def __init__(self, settings: dict, engine: str):
        self.engine = engine
        self.s = settings
        if engine == "anthropic":
            self.model = settings.get("anthropic_model") or "claude-opus-5-5"
        elif engine == "gemini":
            self.model = settings.get("gemini_model") or "gemini-2.5-flash"
            self.base = "https://generativelanguage.googleapis.com/v1beta/openai"
        elif engine == "deepseek":
            self.model = settings.get("deepseek_model") or "deepseek-chat"
            self.base = "https://api.deepseek.com/v1"
        elif engine == "ollama":
            self.model = settings.get("ollama_model") or "qwen2.5:7b"
            self.base = (settings.get("ollama_base_url") or "http://localhost:11434/v1").rstrip("/")
        else:
            self.model = settings.get("openai_model") or "gpt-4o-mini"
            self.base = (settings.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        self.keys = ["ollama"] if engine == "ollama" else api_keys(settings, engine)
        if not self.keys:
            raise ValueError(f"Chưa nhập API key cho {ENGINE_NAMES.get(engine, engine)}. Vào trang Cài đặt → khung "
                             f"{ENGINE_NAMES.get(engine, engine)} để nhập.")
        self.ki = _KEY_POS.get(engine, 0) % len(self.keys)
        self.rpm = 0 if engine == "ollama" else engine_rpm(settings, engine)
        self._json_mode = True
        # Các model Claude đời mới (và một số model OpenAI) không nhận tham số temperature -> không gửi
        self._send_temp = engine != "anthropic"

    @property
    def api_key(self) -> str:
        return self.keys[self.ki]

    # ------------------------------------------------------------------ nhiều key + giới hạn mỗi phút
    def _alive(self, i: int) -> bool:
        d = _KEY_DEAD.get(self.keys[i])
        return not d or time.time() > d[0]

    def _room(self, i: int) -> float:
        """Còn phải chờ bao nhiêu giây thì key i được gửi tiếp (0 = gửi ngay)."""
        if not self.rpm:
            return 0.0
        now = time.time()
        calls = [t for t in _KEY_CALLS.get(self.keys[i], []) if now - t < 60]
        _KEY_CALLS[self.keys[i]] = calls
        return 0.0 if len(calls) < self.rpm else 60.5 - (now - calls[0])

    def _switch(self, why: str) -> bool:
        """Đổi sang key khác còn dùng được (ưu tiên key gửi được ngay). False = không còn key nào."""
        alive = [i for i in range(len(self.keys)) if i != self.ki and self._alive(i)]
        if not alive:
            return False
        nxt = min(alive, key=lambda i: (self._room(i), (i - self.ki) % len(self.keys)))
        _, report = net.current()
        if report:
            report(f"{why} – chuyển sang {_key_tag(self.engine, nxt, len(self.keys))}")
        self.ki = nxt
        _KEY_POS[self.engine] = nxt
        return True

    def _pace(self):
        """Gói miễn phí: không gửi quá `rpm` lần / phút / key – key này hết lượt phút này thì dùng key khác, không có thì chờ."""
        if not self._alive(self.ki):
            self._switch(f"{_key_tag(self.engine, self.ki, len(self.keys))} đang bị chặn")
        wait = self._room(self.ki)
        if wait > 0:
            others = [i for i in range(len(self.keys)) if i != self.ki and self._alive(i) and self._room(i) == 0]
            if others:
                self.ki = others[0]
                _KEY_POS[self.engine] = self.ki
                wait = 0.0
        if wait > 0:
            _, report = net.current()
            if report:
                report(f"Gói miễn phí {ENGINE_NAMES.get(self.engine, self.engine)}: chờ {wait:.0f} s "
                       f"(tối đa {self.rpm} lần gửi / phút mỗi key – thêm key ở Cài đặt để dịch nhanh hơn)")
            net.sleep(wait)
        _KEY_CALLS.setdefault(self.api_key, []).append(time.time())

    @staticmethod
    def _retry_after(r) -> float:
        m = re.search(r'retry[_ -]?delay"?\s*[:=]\s*"?(\d+(?:\.\d+)?)', r.text or "", re.I) or \
            re.search(r"retry in (\d+(?:\.\d+)?)\s*s", r.text or "", re.I)
        if m:
            return min(120.0, float(m.group(1)) + 1)
        try:
            return min(120.0, float(r.headers.get("retry-after", "") or 0)) or 20.0
        except (TypeError, ValueError):
            return 20.0

    def _key_failed(self, r) -> str | None:
        """Lỗi do KEY (sai / hết hạn / hết lượt trong ngày / hết tiền) -> lý do; lỗi khác -> None."""
        t = (r.text or "").lower()
        if r.status_code in (401, 403) or (r.status_code == 400 and any(
                x in t for x in ("api key not valid", "api_key_invalid", "invalid api key", "api key expired",
                                 "expired", "invalid x-api-key"))):
            return "sai hoặc hết hạn"
        if r.status_code == 402 or "insufficient_quota" in t or "insufficient balance" in t or "credit balance" in t:
            return "hết tiền / hết hạn mức"
        if r.status_code == 429 and any(x in t for x in ("perday", "per day", "per_day", "daily", "requests per day")):
            return "hết lượt miễn phí hôm nay"
        return None

    def _http(self, url: str, headers: dict, body: dict):
        # chờ AI viết xong có thể lâu (không có dữ liệu) -> chờ đọc tới 5 phút; rớt mạng thì keepalive báo trong ~40 s
        return net.session().post(url, headers=headers, json=body, timeout=(15, 300))

    def chat(self, system: str, user: str, max_tokens: int = 8000, temperature: float = 0.3) -> str:
        last_err = None
        fails = 0
        waits = 0
        name = ENGINE_NAMES.get(self.engine, self.engine)
        while fails < 4:
            try:
                self._pace()
                if self.engine == "anthropic":
                    body = {"model": self.model, "max_tokens": max(max_tokens, 16000),  # chừa chỗ cho phần suy nghĩ
                            "system": system, "messages": [{"role": "user", "content": user}]}
                    if self._send_temp:
                        body["temperature"] = temperature
                    r = net.call(self._http, "https://api.anthropic.com/v1/messages",
                                 {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"}, body)
                    if r.status_code == 200:
                        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
                else:
                    body = {"model": self.model, "max_tokens": max_tokens,
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
                    if self._send_temp:
                        body["temperature"] = temperature
                    if self._json_mode:
                        body["response_format"] = {"type": "json_object"}
                    r = net.call(self._http, f"{self.base}/chat/completions",
                                 {"Authorization": f"Bearer {self.api_key}"}, body)
                    if r.status_code == 400 and self._json_mode and "response_format" in r.text:
                        self._json_mode = False
                        continue
                    if r.status_code == 200:
                        return r.json()["choices"][0]["message"]["content"] or ""
                if r.status_code == 400 and self._send_temp and "temperature" in r.text:
                    self._send_temp = False  # model không hỗ trợ temperature -> gửi lại không có nó
                    continue
                why = self._key_failed(r)
                if why:   # key hỏng / hết lượt -> đánh dấu, đổi key khác dịch tiếp
                    tag = _key_tag(self.engine, self.ki, len(self.keys))
                    _KEY_DEAD[self.api_key] = (time.time() + (DAY_BLOCK if "hôm nay" in why or "tiền" in why
                                                              else 10 ** 9), why)
                    if self._switch(f"{tag} {why}"):
                        continue
                    if r.status_code in (401, 403) or "hết hạn" in why:
                        raise PermissionError(f"API key {name} không hợp lệ hoặc hết hạn ({r.status_code}) – "
                                              f"đã thử {len(self.keys)} key. Thêm / sửa key ở trang Cài đặt: {r.text[:200]}")
                    raise ApiError(f"Tất cả {len(self.keys)} key {name} đều {why}. Thêm key khác ở trang Cài đặt (khung "
                                   f"{name}) hoặc dịch tiếp sau – câu đã dịch được giữ lại, bấm 'Dịch câu chưa dịch' "
                                   "để dịch nốt.")
                if r.status_code == 429:   # giới hạn mỗi phút -> key khác, không có thì chờ rồi gửi lại
                    if self._switch(f"{_key_tag(self.engine, self.ki, len(self.keys))} bị giới hạn số lần / phút"):
                        continue
                    if waits < WAIT_LIMIT_429:
                        waits += 1
                        wait = self._retry_after(r)
                        _, report = net.current()
                        if report:
                            report(f"{name} báo gửi quá nhanh (gói miễn phí) – chờ {wait:.0f} s rồi gửi tiếp")
                        net.sleep(wait)
                        continue
                if r.status_code == 404:
                    raise ValueError(f"Không tìm thấy model '{self.model}' ({r.text[:300]})")
                last_err = RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            except (PermissionError, ValueError, ApiError):
                raise
            except requests.RequestException as e:
                last_err = e
                # mất mạng giữa chừng: chờ có mạng lại rồi gửi lại lô này (không tính là 1 lần hỏng, không bỏ lô)
                if net.is_net_error(e) and net.wait_online(f"dịch bằng AI ({self.engine})"):
                    continue
            fails += 1
            if fails < 4:
                net.sleep(2 * 2 ** (fails - 1))
        hint = ("Mạng chập chờn / máy chủ không trả lời – thử lại sau ít phút" if last_err is not None
                and net.is_net_error(last_err) else "Kiểm tra Internet / API key / số dư tài khoản ở trang Cài đặt")
        raise ApiError(f"Không gọi được API dịch {self.engine} ({self.model}): {str(last_err)[:250]}. {hint}.")


def list_models(settings: dict, engine: str, key: str = "") -> list[str]:
    """Các model key này dùng được (nút 'Chọn model' ở Cài đặt) – Gemini: model gọi được generateContent."""
    key = key or (api_keys(settings, engine) or [""])[0]
    if engine == "gemini":
        r = net.session().get("https://generativelanguage.googleapis.com/v1beta/models",
                              params={"key": key, "pageSize": 200}, timeout=(10, 30))
        r.raise_for_status()
        return sorted({m["name"].split("/", 1)[-1] for m in r.json().get("models", [])
                       if "generateContent" in (m.get("supportedGenerationMethods") or [])}, reverse=True)
    if engine == "anthropic":
        r = net.session().get("https://api.anthropic.com/v1/models", timeout=(10, 30),
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
    c = LLMClient(dict(settings, **{f"{engine}_api_keys": [key] if key else []}), engine)
    r = net.session().get(f"{c.base}/models", headers={"Authorization": f"Bearer {c.api_key}"}, timeout=(10, 30))
    r.raise_for_status()
    return sorted(m["id"] for m in r.json().get("data", []))


def check_key(settings: dict, engine: str, key: str) -> str:
    """Thử 1 key (nút 'Kiểm tra' ở Cài đặt): trả lời ngắn gọn ✓ / ✗ + lý do."""
    st = dict(settings, **{f"{engine}_api_key": key, f"{engine}_api_keys": [key], f"{engine}_rpm": 0})
    _KEY_DEAD.pop(key, None)
    try:
        c = LLMClient(st, engine)
        c.chat("Reply with JSON only.", 'Return {"ok": 1}', max_tokens=50, temperature=0)
        return f"✓ dùng được ({c.model})"
    except Exception as e:  # noqa: BLE001
        return "✗ " + str(e)[:220]


# ======================================================================= Hy-MT2

LOCAL_ENGINES = ("google", "hymt")    # không phải AI hội thoại: không tự đọc kịch bản, không rút gọn câu được
HYMT_REPO = "tencent/Hy-MT2-1.8B"
_hymt = None


def hymt_available() -> bool:
    from .config import installed

    return installed("transformers", "torch")


class HymtWorker(VieneuWorker):
    """Tiến trình riêng chạy Hy-MT2 (trami/hymt_worker.py), cùng cách điều khiển với VieNeu / Qwen3."""
    title = "Hy-MT2"
    stall_call = 900.0   # 1 lô dịch không có tin gì quá 15 phút -> treo
    flag = "--hymt-worker"
    log_name = "hymt.txt"

    def __init__(self, repo: str, token: str = ""):
        super().__init__(repo)
        self.token = token

    def _extra_env(self) -> dict:
        return {"HF_TOKEN": self.token} if self.token else {}

    def call(self, req: dict, progress=None, cancel=None) -> dict:
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start(progress, cancel)
            assert self.proc and self.proc.stdin
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            r = self._read(progress, cancel, self.stall_call)
        if "error" in r:
            raise RuntimeError(r["error"])
        return r


def hymt_worker(settings: dict | None = None) -> HymtWorker:
    global _hymt
    st = settings or {}
    repo = st.get("hymt_model") or HYMT_REPO
    if _hymt is None or _hymt.mode != repo:
        _hymt = HymtWorker(repo, st.get("hf_token", ""))
        import atexit

        atexit.register(_hymt.stop)
    return _hymt


# ======================================================================= Translator

class Translator:
    def __init__(self, settings: dict, engine: str | None = None, style: str | None = None,
                 glossary: dict[str, str] | None = None, cache: dict | None = None, target: str | None = None,
                 source: str | None = None, skill: str = ""):
        self.s = settings
        self.skill = skill or ""   # kỹ năng dịch của người dùng (skills.py) – gắn vào lời nhắc của mọi lần hỏi AI
        self.engine = engine or settings.get("translate_engine", "google")
        self.style = style or settings.get("translate_style", "auto")
        self.target = langs.norm(target or settings.get("target_lang"))
        self.source = source or "zh"
        # bảng thuật ngữ của bạn là tên Hán Việt -> chỉ dùng khi dịch sang tiếng Việt; tiếng khác dùng bảng riêng
        self.glossary = dict(parse_glossary(settings.get("glossary", ""))) if self.target == "vi" else {}
        if glossary:
            self.glossary.update(glossary)
        self.cache = cache if cache is not None else {}
        self.batch = engine_batch(settings, self.engine)   # số câu mỗi lần gửi (khung riêng từng công cụ ở Cài đặt)
        self.llm = None if self.engine in LOCAL_ENGINES else LLMClient(settings, self.engine)
        self.fit_dub = bool(settings.get("dub_fit", True))   # dịch vừa thời lượng để lồng tiếng khớp
        self.fallback_lines = 0   # số câu AI trả lỗi định dạng, phải dùng Google bù

    # ------------------------------------------------------------- public
    def translate_segments(self, segs: list[Segment], progress: ProgressCB | None = None,
                           cancel: Callable[[], bool] | None = None, only_empty: bool = False,
                           auto_terms: bool = True, summary: str = "", rows: set[int] | None = None) -> list[Segment]:
        """rows: chỉ dịch các câu này (None = mọi câu)."""
        idx = [i for i, s in enumerate(segs) if s.text.strip() and (not only_empty or not s.trans.strip())
               and (rows is None or i in rows)]
        if not idx:
            return segs
        if self.llm and auto_terms:
            if progress:
                progress(0.01, "AI đang đọc toàn bộ kịch bản để thống nhất tên riêng, xưng hô…")
            try:
                summary = summary or self.analyze([segs[i].text for i in idx])
            except (Cancelled, net.NetworkDown):
                raise
            except Exception:
                summary = summary or ""
        texts = [segs[i].text for i in idx]
        if self.llm and self.fit_dub:
            # dịch vừa thời lượng: mỗi câu kèm giới hạn độ dài theo thời gian nhân vật nói (đơn vị theo tiếng đích)
            unit = langs.INFO[self.target]["unit_vi"] if self.target == "vi" else langs.INFO[self.target]["unit_ai"]
            texts = [f"{segs[i].text} ⟪≤{budget_for(segs[i].duration, 1.1, self.target)} {unit}⟫" for i in idx]
        results = self.translate_lines(texts, progress, cancel, summary=summary)
        for i, t in zip(idx, results):
            segs[i].trans = BUDGET_RE.sub(" ", t or "").strip()
            segs[i].extra.pop("trans_user", None)
            if segs[i].trans and not untranslated(segs[i].trans, self.target):   # nhớ theo câu gốc -> không tốn API
                self.cache.setdefault(cache_key(self.engine, self.style, self.target, segs[i].text), segs[i].trans)
        return segs

    def shorten(self, items: list[tuple[str, str, int]]) -> list[str]:
        """Rút gọn bản dịch quá dài so với thời gian nói. items: (câu gốc, bản dịch, số tiếng tối đa)."""
        if not self.llm or not items:
            return [vi for _, vi, _ in items]
        L = langs.INFO[self.target]
        if self.target == "vi":
            payload = {str(k + 1): {"goc": zh, "dich": vi, "toi_da": f"{n} tiếng"} for k, (zh, vi, n) in enumerate(items)}
            sysp, user = SHORTEN_PROMPT, "RÚT GỌN CÁC BẢN DỊCH SAU:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        else:
            payload = {str(k + 1): {"source": zh, "translation": vi, "max": f"{n} {L['unit_ai']}"}
                       for k, (zh, vi, n) in enumerate(items)}
            sysp = SHORTEN_PROMPT_EN.format(lang=L["english"], unit=L["unit_ai"])
            user = "SHORTEN THESE TRANSLATIONS:\n" + json.dumps(payload, ensure_ascii=False, indent=0)
        try:
            d = _extract_json(self.llm.chat(with_skill(sysp, self.skill), user))
        except (Cancelled, net.NetworkDown):
            raise
        except Exception:  # noqa: BLE001
            return [vi for _, vi, _ in items]
        out = []
        for k, (_, vi, n) in enumerate(items):
            v = d.get(str(k + 1)) if isinstance(d, dict) else None
            v = BUDGET_RE.sub(" ", v).strip() if isinstance(v, str) else ""
            out.append(v if v and syllables(v, self.target) < syllables(vi, self.target) else vi)
        return out

    def translate_lines(self, lines: list[str], progress: ProgressCB | None = None,
                        cancel: Callable[[], bool] | None = None, summary: str = "") -> list[str]:
        out: list[str | None] = [None] * len(lines)
        todo = []
        for i, l in enumerate(lines):
            hit = self.cache.get(cache_key(self.engine, self.style, self.target, l))
            if hit and not untranslated(hit, self.target):   # bản dịch rỗng / còn chữ Hán (lần trước lỗi) -> dịch lại
                out[i] = hit
            else:
                todo.append(i)
        done = len(lines) - len(todo)
        if self.engine == "hymt":
            res = self._hymt(lines, todo, progress, cancel, done)
            for gi, r in zip(todo, res):
                out[gi] = r
                if r and not untranslated(r, self.target):
                    self.cache[cache_key(self.engine, self.style, self.target, lines[gi])] = r
        elif self.engine == "google":
            batches = self._google_batches([lines[i] for i in todo])
            pos = 0
            for chunk in batches:
                if cancel and cancel():
                    raise Cancelled()
                res = self._google(chunk)
                for j, r in enumerate(res):
                    gi = todo[pos + j]
                    out[gi] = r
                    if r:
                        self.cache[cache_key(self.engine, self.style, self.target, lines[gi])] = r
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
                    if r and not untranslated(r, self.target):
                        self.cache[cache_key(self.engine, self.style, self.target, lines[gi])] = r
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
        L = langs.INFO[self.target]
        for ch in chunks[:6]:  # đọc tối đa ~40k ký tự đầu là đủ nắm nhân vật
            if self.target == "vi":
                sysp = "Bạn là chuyên gia Hán Việt và biên dịch phim Trung Quốc. Chỉ trả JSON."
                prompt = (
                    "Đọc đoạn kịch bản phụ đề tiếng Trung dưới đây và trả về JSON với các khoá:\n"
                    '"genre": thể loại phim (cổ trang/tiên hiệp/hiện đại/review/...)\n'
                    '"terms": object ánh xạ TÊN RIÊNG tiếng Trung (tên người, biệt danh, địa danh, môn phái, tổ chức, '
                    "chiêu thức, bảo vật) -> phiên âm Hán Việt chuẩn. Chỉ lấy tên riêng thực sự, không lấy từ thông thường.\n"
                    '"relations": mô tả ngắn quan hệ giữa các nhân vật chính và cách xưng hô tiếng Việt phù hợp.\n\n'
                    "KỊCH BẢN:\n" + ch)
            else:
                sysp = f"You are an expert translator of Chinese films into {L['english']}. Return JSON only."
                prompt = (
                    "Read the subtitle script below and return JSON with the keys:\n"
                    '"genre": the film genre\n'
                    f'"terms": object mapping PROPER NAMES in the source (people, nicknames, places, sects, '
                    f"organizations, techniques, treasures) -> their standard {L['english']} rendering "
                    f"({_NAMES.get(self.target, '')}). Only real proper names, no common words.\n"
                    f'"relations": short description of the main characters\' relationships and suitable forms of '
                    f"address in {L['english']}.\n\nSCRIPT:\n" + ch)
            try:
                d = _extract_json(self.llm.chat(sysp, prompt, max_tokens=4000, temperature=0.1))
            except (Cancelled, net.NetworkDown):
                raise
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
        vi = self.target == "vi"
        lab = (("PHONG CÁCH", "THÔNG TIN PHIM", "BẢNG THUẬT NGỮ (bắt buộc dùng)",
                "NGỮ CẢNH TRƯỚC (đã dịch, chỉ để tham khảo, KHÔNG dịch lại)", "NGỮ CẢNH SAU (chỉ tham khảo)") if vi else
               ("STYLE", "FILM NOTES", "GLOSSARY (must use)", "PREVIOUS CONTEXT (already translated, reference only, "
                "do NOT translate again)", "FOLLOWING CONTEXT (reference only)"))
        parts = [f"{lab[0]}: {(STYLES if vi else STYLES_EN).get(self.style, (STYLES if vi else STYLES_EN)['auto'])}"]
        if summary:
            parts.append(f"{lab[1]}:\n{summary}")
        if gl:
            parts.append(f"{lab[2]}:\n" + glossary_text(gl))
        if ctx_before:
            parts.append(f"{lab[3]}:\n" + "\n".join(f"- {a} => {b}" for a, b in ctx_before))
        if ctx_after:
            parts.append(f"{lab[4]}:\n" + "\n".join(f"- {a}" for a in ctx_after))
        payload = {str(i + 1): l for i, l in enumerate(lines)}
        ask = (f"DỊCH {len(lines)} DÒNG SAU sang tiếng Việt, trả về JSON với đúng các khoá 1..{len(lines)}:" if vi else
               f"TRANSLATE THE FOLLOWING {len(lines)} LINES into {langs.INFO[self.target]['english']}, return JSON with "
               f"exactly the keys 1..{len(lines)}:")
        parts.append(ask + "\n" + json.dumps(payload, ensure_ascii=False, indent=0))
        user = "\n\n".join(parts)
        try:
            d = _extract_json(self.llm.chat(with_skill(system_prompt(self.target, self.source), self.skill), user))
        except (Cancelled, net.NetworkDown):
            raise
        except (PermissionError, ApiError, ValueError) as e:
            if isinstance(e, ValueError) and "JSON" in str(e):
                d = None  # AI trả về sai định dạng -> chia nhỏ lô rồi thử lại
            else:
                raise     # lỗi kết nối / key / model: báo rõ, KHÔNG tự đổi sang Google
        except Exception:
            d = None
        if d is not None:
            res = [(d.get(str(i + 1)) or "").strip() if isinstance(d.get(str(i + 1)), str) else "" for i in range(len(lines))]
            miss = [i for i, r in enumerate(res) if (not r or untranslated(r, self.target)) and lines[i].strip()]
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
        except (Cancelled, net.NetworkDown):
            raise
        except Exception:
            return [""] * len(lines)

    # ------------------------------------------------------------- Hy-MT2 (trên máy)
    def _hymt(self, lines: list[str], todo: list[int], progress: ProgressCB | None, cancel, done0: int) -> list[str]:
        """Dịch bằng Hy-MT2 trong tiến trình riêng: mỗi câu kèm 3 câu trước làm ngữ cảnh + tên riêng có trong câu.
        Xong thì tắt tiến trình để trả bộ nhớ GPU cho bước lồng tiếng."""
        if not todo:
            return []
        w = hymt_worker(self.s)
        res: list[str] = []
        step = 32
        try:
            for b in range(0, len(todo), step):
                if cancel and cancel():
                    raise Cancelled()
                ids = todo[b:b + step]
                items = [{"text": lines[i],
                          "context": [lines[k] for k in range(max(0, i - 3), i) if lines[k].strip()],
                          "terms": [[k, v] for k, v in self.glossary.items() if k in lines[i]][:12]} for i in ids]
                base = done0 + b

                def pr(x: float, msg: str, base=base, n=len(ids)):
                    if progress and "dịch" in msg:
                        progress((base + x * n) / max(1, len(lines)), f"Hy-MT2 đang dịch {int(base + x * n)}/"
                                 f"{len(lines)} câu (trên máy)")
                    elif progress:
                        progress(base / max(1, len(lines)), msg)
                r = w.call({"items": items, "target": langs.INFO[self.target]["hymt"]}, pr, cancel)
                res += [str(x) for x in r.get("out", [])][:len(ids)] + [""] * max(0, len(ids) - len(r.get("out", [])))
        finally:
            w.stop()
        return res

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

    def _gl(self) -> tuple[str, str]:
        """(nguồn, đích) cho Google: nguồn theo ngôn ngữ gốc của dự án (trước đây ghi cứng tiếng Trung)."""
        sl = {"zh": "zh-CN", "yue": "zh-TW", "en": "en", "ja": "ja", "ko": "ko"}.get(self.source, "auto")
        return sl, langs.INFO[self.target]["google"]

    def _gtx(self, text: str) -> str:
        """Google Translate (endpoint miễn phí trả JSON)."""
        sl, tl = self._gl()
        r = net.session().post("https://translate.googleapis.com/translate_a/single",
                               params={"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "ie": "UTF-8", "oe": "UTF-8"},
                               data={"q": text}, timeout=(8, 25),
                               headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        data = r.json()
        return "".join(part[0] for part in (data[0] or []) if part and part[0])

    def _clients5(self, text: str) -> str:
        sl, tl = self._gl()
        r = net.session().get("https://clients5.google.com/translate_a/t",
                              params={"client": "dict-chrome-ex", "sl": sl, "tl": tl, "q": text}, timeout=(8, 25),
                              headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        r.raise_for_status()
        d = r.json()
        if isinstance(d, list) and d:
            x = d[0]
            return x[0] if isinstance(x, list) else str(x)
        if isinstance(d, dict) and d.get("sentences"):
            return "".join(s.get("trans", "") for s in d["sentences"])
        raise ValueError("Phản hồi Google không hợp lệ")

    def _deep(self, text: str) -> str:
        from deep_translator import GoogleTranslator

        sl, tl = self._gl()
        return GoogleTranslator(source=sl, target=tl).translate(text) or ""

    def _gt_one(self, text: str) -> str:
        errs: list[str] = []
        fails = 0
        while True:
            net_err = False
            for fn in (self._gtx, self._clients5, self._deep):
                try:
                    return net.call(fn, text)
                except Cancelled:
                    raise
                except Exception as e:  # noqa: BLE001
                    errs.append(f"{fn.__name__.strip('_')}: {str(e)[:150]}")
                    net_err = net_err or net.is_net_error(e)
            # mất mạng: chờ có mạng lại rồi dịch tiếp câu này (không tính là 1 lần hỏng)
            if net_err and net.wait_online("dịch bằng Google"):
                continue
            fails += 1
            if fails >= 3:
                raise RuntimeError("Không kết nối được Google Dịch (kiểm tra mạng hoặc bị giới hạn tạm thời, "
                                   "hãy thử lại sau hoặc dùng engine AI): " + errs[0][:200])
            net.sleep(2 * 2 ** (fails - 1))

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


def translate_text(text: str, settings: dict, engine: str | None = None, target: str | None = None,
                   source: str | None = None) -> str:
    return Translator(settings, engine, target=target, source=source).translate_lines([text])[0] if text.strip() else ""
