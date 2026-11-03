"""Câu OCR "không đạt" -> AI xem ảnh đọc lại + bộ nghe xác nhận, chọn theo 2 trên 3 nguồn (2.1.44).

Câu không đạt = OCR đọc chắc dưới mức (`ocr_pick_conf`) hoặc suốt lúc câu hiện không có tiếng nói (giọng đã tách). Mỗi câu
có tới 3 bản: chữ OCR đọc, chữ AI đọc trên ảnh dải phụ đề (Gemini / OpenAI / Claude / Ollama mô hình xem ảnh), lời bộ nghe
nghe được đúng khúc giờ câu. So khớp theo CHỮ và theo ÂM ĐỌC (pinyin – bộ nghe hay nghe nhầm chữ đồng âm, phụ đề hay viết
gọn hơn lời nói), lấy mức cao hơn. GIỜ CÂU LUÔN GIỮ THEO OCR – chỉ chữ được sửa.

Bộ nghe chạy TRƯỚC (miễn phí, trên máy): câu OCR khớp lời nói -> xác nhận, không cần gửi AI (đỡ tốn lượt AI). Câu còn lại
mới gửi AI (10–12 câu 1 lần, mỗi câu 1–2 ảnh nhỏ cắt đúng vùng quét – không gửi video), rồi:

  AI ≈ giọng nói, OCR khác                         -> lấy câu AI ("ai_fix")
  OCR ≈ giọng nói                                  -> giữ OCR ("same")
  không có tiếng nói + AI báo không phải phụ đề     -> xoá (cất "Khôi phục câu đã xoá") ("drop")
  không có tiếng nói + AI đọc ra phụ đề            -> giữ, tô màu xem lại ("novoice"; AI khác OCR -> bản AI để chọn)
  AI = OCR (có / không có giọng mà giọng không khớp) -> giữ, AI xác nhận ("ai_same") / tô xanh nếu giọng khác ("check")
  còn lại                                          -> giữ OCR, tô xanh, bản AI để "Dùng bản AI" ("check")
Không bao giờ xoá khi chỉ 1 nguồn bảo xoá. Không có AI -> cách 2.1.42 (chỉ nghe). Không có bộ nghe -> chỉ AI: AI = OCR thì
xác nhận, khác thì tô xanh (không đổi chữ, không xoá).
"""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

from .dual import core

VISION = ("gemini", "openai", "anthropic", "ollama")      # AI đọc được ảnh (DeepSeek: API chỉ nhận chữ)
CHOICES = [("auto", "Theo công cụ dịch"), ("off", "Tắt – chỉ nghe lại")]
BATCH = 10               # số câu mỗi lần gửi AI
MAX_W = 960              # ảnh dải phụ đề gửi AI: rộng tối đa
MATCH = 0.7              # khớp từ mức này (chữ hoặc âm đọc) = cùng 1 câu (Cài đặt ocr_ai_match)
NOVOICE_FRAC = 0.08      # câu có tiếng nói < 8 % thời gian hiện = "không có tiếng nói"

PROMPT = """Bạn chép lại PHỤ ĐỀ in sẵn trên video (thường là tiếng Trung). Mỗi câu có 1–2 ảnh là dải phụ đề cắt từ các khung
hình lúc câu đó đang hiện (đã đánh số "Câu N").
Quy tắc:
1. Chỉ chép DÒNG PHỤ ĐỀ (lời thoại / thuyết minh) – thường nằm giữa khung, cùng 1 kiểu chữ, có viền. BỎ chữ trong cảnh
   (biển hiệu, bảng điểm / số liệu game, logo, watermark, tên kênh, chữ chạy, chữ trên đồ vật).{color}
2. Chép ĐÚNG từng chữ như trên ảnh, giữ giản thể / phồn thể như ảnh, KHÔNG dịch, KHÔNG sửa câu cho hay hơn, KHÔNG thêm
   dấu câu ảnh không có. Phụ đề 2 dòng thì nối liền (dòng trên trước).
3. Nhiều ảnh của cùng 1 câu là các khung khác nhau của CÙNG câu – chọn bản rõ nhất.
4. Ảnh không có dòng phụ đề nào (chỉ có chữ trong cảnh / không có chữ) -> "phu_de": false, "chu": "".
5. Chữ mờ không chắc vẫn chép theo cách đọc hợp lý nhất, "chac": false.
Trả về JSON đủ mọi số câu: {"1": {"phu_de": true, "chu": "…", "chac": true}, "2": {…}}"""


def char_sim(a: str, b: str) -> float:
    x, y = core(a), core(b)
    return SequenceMatcher(None, x, y, autojunk=False).ratio() if x and y else 0.0


def match(a: str, b: str) -> float:
    """Độ khớp 2 câu (0–1): giống theo chữ hoặc theo âm đọc (pinyin không dấu thanh), lấy mức cao hơn."""
    from .vreview import _sounds_alike

    x, y = core(a), core(b)
    if not x or not y:
        return 0.0
    return max(SequenceMatcher(None, x, y, autojunk=False).ratio(), _sounds_alike(x, y))


def engine_choices(settings: dict) -> list[tuple[str, str]]:
    from .translate import ENGINE_NAMES, api_keys

    out = list(CHOICES)
    for eng in ("gemini", "openai", "anthropic", "ollama"):
        ok = eng == "ollama" or bool(api_keys(settings, eng))
        out.append((eng, ENGINE_NAMES.get(eng, eng) + ("" if ok else " – chưa có API key") +
                    (" (cần mô hình xem ảnh, vd qwen2.5-vl)" if eng == "ollama" else "")))
    return out


def pick_engine(settings: dict) -> tuple[str | None, str]:
    """AI đọc lại ảnh: (công cụ | None, lý do / tên). auto = công cụ dịch đang chọn nếu xem được ảnh và có key, không
    thì AI xem ảnh đầu tiên có key (Gemini > OpenAI > Claude)."""
    from .translate import ENGINE_NAMES, api_keys

    v = settings.get("ocr_ai_engine", "auto") or "auto"
    if v == "off":
        return None, "đã tắt AI đọc lại"
    if v == "auto":
        tr = settings.get("translate_engine", "")
        cands = ([tr] if tr in VISION and tr != "ollama" else []) + ["gemini", "openai", "anthropic"]
        for eng in cands:
            if api_keys(settings, eng):
                return eng, ENGINE_NAMES.get(eng, eng)
        return None, "chưa có API key của AI xem được ảnh (Gemini / OpenAI / Claude) trong Cài đặt → Công cụ dịch"
    if v not in VISION:
        return None, f"{ENGINE_NAMES.get(v, v)} không đọc được ảnh"
    if v != "ollama" and not api_keys(settings, v):
        return None, f"chưa có API key {ENGINE_NAMES.get(v, v)} trong Cài đặt → Công cụ dịch"
    return v, ENGINE_NAMES.get(v, v)


# ------------------------------------------------------------------ ảnh dải phụ đề
def frame_times(seg) -> list[float]:
    """Khung chụp cho 1 câu: giữa câu; câu dài ≥ 2 s thêm 1 khung (chữ đổi màu / nền đổi -> AI có 2 bản để chọn)."""
    a, b = float(seg.start), float(seg.end)
    d = b - a
    if d >= 2.0:
        return [a + 0.35 * d, a + 0.7 * d]
    return [a + 0.5 * d]


def jpeg_of(src, t: float, region: dict | None, max_w: int = MAX_W) -> bytes:
    """1 khung tại giây t, cắt đúng vùng quét, JPEG (rộng ≤ max_w)."""
    from .ffmpeg import ffmpeg_bin
    from .ocrcolor import _crop_vf

    vf = _crop_vf(region) + f",scale='min(iw,{int(max_w)})':-2"
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
           "-frames:v", "1", "-an", "-vf", vf, "-q:v", "4", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(cmd, capture_output=True, creationflags=flags).stdout


def grab_all(src, segs, rows: list[int], region: dict | None, progress=None, cancel=None) -> dict[int, list[bytes]]:
    """Ảnh của mọi câu cần đọc lại (4 tiến trình ffmpeg cùng lúc)."""
    from .ffmpeg import Cancelled

    jobs = [(i, t) for i in rows for t in frame_times(segs[i])]
    out: dict[int, list[bytes]] = {i: [] for i in rows}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [(i, ex.submit(jpeg_of, src, t, region)) for i, t in jobs]
        for k, (i, f) in enumerate(futs):
            if cancel and cancel():
                raise Cancelled()
            data = f.result()
            if data:
                out[i].append(data)
            if progress and k % 8 == 0:
                progress(k / max(1, len(futs)), f"Chụp ảnh phụ đề {k + 1}/{len(futs)}…")
    return out


# ------------------------------------------------------------------ AI đọc ảnh
def _parse(reply: str) -> dict:
    from .skills import _json

    try:
        d = _json(reply)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def read_lines(settings: dict, engine: str, images: dict[int, list[bytes]], color_desc: str = "",
               progress=None, cancel=None) -> tuple[dict[int, tuple[str, bool, bool]], dict]:
    """AI đọc lại ảnh từng câu. Trả ({câu: (chữ, có phụ đề không, AI chắc không)}, thống kê {calls, sent, failed,
    error}). Lô lỗi (mạng / key / AI trả sai dạng) -> các câu lô đó không có bản AI (giữ như chưa gửi)."""
    from .asr import to_simplified
    from .ffmpeg import Cancelled
    from .translate import LLMClient

    client = LLMClient(settings, engine)
    client._json_mode = engine != "anthropic"
    rows = [i for i in images if images[i]]
    color = (f"\n   Màu phụ đề của video này: {color_desc}." if color_desc else "")
    system = PROMPT.replace("{color}", color)
    res: dict[int, tuple[str, bool, bool]] = {}
    st = {"calls": 0, "sent": 0, "failed": 0, "error": ""}
    for g in range(0, len(rows), BATCH):
        if cancel and cancel():
            raise Cancelled()
        part = rows[g:g + BATCH]
        if progress:
            progress(g / max(1, len(rows)), f"AI đọc lại phụ đề câu {g + 1}–{g + len(part)}/{len(rows)}…")
        imgs = []
        for n, i in enumerate(part, 1):
            for k, data in enumerate(images[i]):
                imgs.append((f"Câu {n}" + (f" – ảnh {k + 1}" if len(images[i]) > 1 else ""), data))
        user = f"Có {len(part)} câu, ảnh đánh số theo câu bên dưới. Trả JSON đủ {len(part)} số câu."
        st["calls"] += 1
        st["sent"] += len(part)
        try:
            d = _parse(client.chat(system, user, max_tokens=300 + 90 * len(part), temperature=0.0, images=imgs))
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001 – lô lỗi: các câu này giữ như chưa có bản AI
            from .config import log_error

            log_error("AI đọc lại phụ đề OCR")
            st["failed"] += len(part)
            st["error"] = f"{type(e).__name__}: {e}"[:200]
            if isinstance(e, (PermissionError, ValueError)):     # key sai / model không có -> dừng, không gửi tiếp
                st["failed"] += max(0, len(rows) - g - len(part))
                break
            continue
        for n, i in enumerate(part, 1):
            v = d.get(str(n))
            if not isinstance(v, dict):
                continue
            txt = str(v.get("chu") or v.get("text") or "").strip().replace(" ", "")
            sub = v.get("phu_de", v.get("subtitle", bool(txt)))
            sub = bool(sub) and bool(core(txt))
            t2 = to_simplified(txt)
            res[i] = (t2 if len(t2) == len(txt) else txt, sub, bool(v.get("chac", True)))
    return res, st


# ------------------------------------------------------------------ quyết định 2 trên 3
def decide(ocr: str, ai: str | None, ai_sub: bool, heard: str | None, hconf: float, thr: float,
           m: float = MATCH) -> str:
    """Kết quả 1 câu: "same" | "ai_fix" | "ai_same" | "check" | "novoice" | "drop" | "" (không đủ dữ liệu – giữ).
    heard None = không có bộ nghe; "" = có nghe mà không ra lời. ai None = không có bản AI."""
    has_voice = heard is not None and bool(core(heard))
    sure = has_voice and hconf >= thr
    if ai is None:      # chưa có bản AI: chỉ xác nhận khi lời nói giống CHỮ OCR (khớp âm đọc chưa đủ – OCR hay đọc
        # sai chữ đồng âm / gần hình, phải để AI đọc lại)
        return "same" if sure and char_sim(ocr, heard or "") >= m else ""
    ai_empty = not ai_sub or not core(ai)
    if sure:
        mo, ma = match(ocr, heard or ""), (0.0 if ai_empty else match(ai, heard or ""))
        # AI khớp lời nói hơn OCR; cùng khớp theo âm đọc (OCR đọc sai chữ đồng âm "糊字" / lời nói "胡子") thì bản GIỐNG
        # CHỮ lời nghe được hơn thắng
        if not ai_empty and ma >= m and core(ai) != core(ocr) and (
                ma > mo + 0.05 or (ma >= mo and char_sim(ai, heard or "") > char_sim(ocr, heard or "") + 0.1)):
            return "ai_fix"
        if mo >= m:
            return "same"
        return "check"
    if heard is not None and not has_voice and ai_empty:
        return "drop"                      # 2 nguồn: không có tiếng nói + AI không thấy phụ đề
    if ai_empty:
        return "check"
    if match(ai, ocr) >= m:
        return "novoice" if heard is not None and not has_voice else "ai_same"
    return "novoice" if heard is not None and not has_voice else "check"


def apply(seg, kind: str, ai: str | None, heard: str | None, hconf: float, engine: str, ocr: str) -> None:
    """Đánh dấu + sửa 1 câu theo kết quả (không xoá – câu "drop" bên gọi bỏ)."""
    from .recheck import ocr_fill

    ex = seg.extra
    ex["ocr_ai"] = {"kind": kind, "eng": engine, "ai": ai, "heard": heard, "hconf": round(float(hconf or 0.0), 3),
                    "ocr": ocr}
    if heard is not None and core(heard):
        ex["asr_conf"] = round(float(hconf or 0.0), 3)
    if kind == "ai_fix":
        ocr_fill(seg, ai or "")
        ex["asr_same"] = True               # AI + giọng nói cùng xác nhận chữ mới
        ex.pop("asr_unsure", None)
    elif kind in ("same", "ai_same"):
        ex["asr_same"] = True
    elif kind in ("check", "novoice"):
        alt = ai if ai and core(ai) and core(ai) != core(seg.text) else None
        if alt:
            ex["asr_alt"], ex["asr_alt_eng"] = alt, "ai"
            ex["asr_unsure"] = True
        elif kind == "check" and heard and core(heard) and core(heard) != core(seg.text):
            ex["asr_alt"], ex["asr_alt_eng"] = heard, "recheck"
            ex["asr_unsure"] = True


def voiced_frac(env, hop: float, a: float, b: float, thr_db: float) -> float:
    """Phần thời gian [a, b] giọng đã tách có tiếng (độ to ≥ thr_db)."""
    i0, i1 = int(max(0.0, a) / hop), int(max(a, b) / hop) + 1
    w = env[i0:i1]
    return float((w >= thr_db).mean()) if len(w) else 0.0


def voice_floor(env) -> float:
    """Ngưỡng "có tiếng nói" của cả phim: dưới mức to (phân vị 95) 35 dB, không thấp hơn −55 dBFS."""
    import numpy as np

    if env is None or not len(env):
        return 0.0
    return max(-55.0, float(np.percentile(env, 95)) - 35.0)


KIND_LABEL = {"ai_fix": "AI sửa – giọng nói xác nhận", "same": "giọng nói xác nhận chữ OCR",
              "ai_same": "AI đọc ra giống OCR", "check": "3 bản không khớp – nên xem lại",
              "novoice": "không có tiếng nói lúc câu hiện", "drop": "không có tiếng nói + AI không thấy phụ đề",
              "user_ocr": "bạn chọn lại chữ OCR"}


def note(st: dict | None) -> str:
    """Thông báo sau bước 1: '12 câu OCR không đạt: giọng nói xác nhận 7 · AI sửa 2 · xem lại 2 · xoá 1 (…) · AI
    Gemini 1 lần gửi'."""
    if not st or not st.get("checked"):
        return ""
    out = [f"{st['checked']} câu OCR không đạt"]
    if st.get("novoice_rows"):
        out[0] += f" (trong đó {st['novoice_rows']} câu không có tiếng nói)"
    parts = []
    for k, t in (("same", "giọng nói xác nhận"), ("ai_same", "AI đọc giống OCR"), ("ai_fix", "AI sửa (giọng nói xác "
                 "nhận)"), ("fixed", "điền theo giọng nói"), ("check", "tô xanh – nên xem lại"),
                 ("novoice", "giữ, không có tiếng nói – nên xem lại"), ("dropped", "xoá")):
        if st.get(k):
            parts.append(f"{t} {st[k]}")
    if parts:
        out.append(", ".join(parts))
    if st.get("dropped"):
        out.append("lấy lại câu đã xoá: tab Phụ đề → Công cụ → Lời hát & nghe nhầm → 'Khôi phục câu đã xoá'")
    if st.get("ai_eng"):
        a = f"AI {st['ai_eng']} đọc lại {st.get('ai_sent', 0)} câu ({st.get('ai_calls', 0)} lần gửi)"
        if st.get("ai_failed"):
            a += f" – ⚠ {st['ai_failed']} câu AI lỗi ({st.get('ai_error', '')})"
        out.append(a)
    elif st.get("ai_why"):
        out.append(f"không có AI đọc lại ({st['ai_why']})")
    if st.get("asr_why"):
        out.append(f"không nghe lại được ({st['asr_why']})")
    return " · ".join(out)
