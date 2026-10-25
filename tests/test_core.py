"""Kiểm thử tự động: chạy bằng `python -m pytest -q tests`."""
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TRAMI_DATA", tempfile.mkdtemp(prefix="trami_test_"))

from trami import ffmpeg as ff  # noqa: E402
from trami import subtitle as st  # noqa: E402
from trami.subtitle import Segment  # noqa: E402


# ------------------------------------------------------------------ helpers

@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("vid")
    out = d / "sample.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=6",
                    "-vf", "drawbox=x=100:y=290:w=440:h=40:color=white:t=fill",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)], check=True)
    return out


# ------------------------------------------------------------------ phụ đề

def test_srt_roundtrip(tmp_path):
    segs = [Segment(1.0, 2.5, "你好", "Xin chào"), Segment(3, 4.2, "再见", "Tạm biệt")]
    p = st.save_subtitles(segs, tmp_path / "a.srt", "trans")
    back = st.load_subtitles(p)
    assert [s.text for s in back] == ["Xin chào", "Tạm biệt"]
    assert abs(back[0].start - 1.0) < 1e-3 and abs(back[1].end - 4.2) < 1e-3


def test_ass_and_vtt(tmp_path):
    segs = [Segment(0.5, 2, "你好世界", "Xin chào thế giới")]
    ass = st.save_subtitles(segs, tmp_path / "a.ass", "both", {"box": True}, 1280, 720)
    txt = ass.read_text("utf-8")
    assert "PlayResX: 1280" in txt and "Xin chào thế giới" in txt and "你好世界" in txt
    assert st.load_subtitles(ass)[0].start == 0.5
    vtt = st.save_subtitles(segs, tmp_path / "a.vtt")
    assert vtt.read_text("utf-8").startswith("WEBVTT")


def test_gbk_srt(tmp_path):
    f = tmp_path / "gbk.srt"
    f.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n你好，世界\n".encode("gb18030"))
    assert st.load_subtitles(f)[0].text == "你好，世界"


def test_merge_split_shift():
    segs = [Segment(0, 1, "你好", "Xin chào"), Segment(1, 2, "朋友", "bạn")]
    st.merge(segs, 0)
    assert len(segs) == 1 and segs[0].end == 2 and segs[0].trans == "Xin chào bạn" and segs[0].text == "你好朋友"
    cut = [Segment(0, 1, "我可是最强"), Segment(2, 3, "AI。你先别最强了")]
    assert st.merge(cut, 0)[0].text == "我可是最强AI。你先别最强了"      # câu bị cắt: nối thẳng, không chèn "，"
    assert st.merge([Segment(0, 1, "用GPT"), Segment(1, 2, "AI写")], 0)[0].text == "用GPT AI写"
    st.split(segs, 0, 1.0)
    assert len(segs) == 2 and segs[0].end == 1.0
    st.shift(segs, 0.5)
    assert segs[0].start == 0.5


def test_normalize_overlap():
    segs = st.normalize([Segment(0, 3, "a"), Segment(2, 4, "b"), Segment(5, 5.1, "c")])
    assert segs[0].end <= segs[1].start and segs[2].end - segs[2].start >= 0.4


def test_wrap_line():
    w = st.wrap_line("Sư phụ, khi nào chúng ta mới có thể lên đường đi Tây Thiên thỉnh kinh", 40)
    assert "\n" in w and all(len(x) <= 45 for x in w.split("\n"))


# ------------------------------------------------------------------ cắt ghép

def test_keep_ranges_and_remap():
    from trami.video import keep_ranges

    k = keep_ranges(100, {"start": 10, "end": 90}, [[20, 30], [50, 55]])
    assert k == [(10, 20), (30, 50), (55, 90)]
    segs = st.remap_after_cut([Segment(31, 33, "x"), Segment(56, 57, "y"), Segment(22, 25, "cut")], k)
    assert [s.text for s in segs] == ["x", "y"]
    assert abs(segs[0].start - 11) < 1e-6 and abs(segs[1].start - 31) < 1e-6


def test_target_size():
    from trami.video import target_size

    assert target_size(1920, 1080, "9:16", "1080p") == (1080, 1920)
    assert target_size(1920, 1080, "original", "720p") == (1280, 720)
    assert target_size(720, 1280, "original", "original") == (720, 1280)


# ------------------------------------------------------------------ ASR

def test_resegment_chinese():
    from trami.asr import resegment

    words = []
    t = 0.0
    for w in "大家好，我是李逍遥。今天我们去蜀山拜师学艺，希望能学到真本事。":
        words.append({"start": t, "end": t + 0.2, "word": w})
        t += 0.2
    segs = resegment(words, max_chars=12)
    assert len(segs) >= 3
    assert all(len(s.text) <= 13 for s in segs)
    assert segs[0].text.startswith("大家好")


def test_hallucination_filter():
    from trami.asr import is_hallucination

    assert is_hallucination("字幕由Amara.org社区提供")
    assert is_hallucination("啊啊啊啊啊啊啊啊啊啊")
    assert not is_hallucination("师父，我们什么时候出发？")


def test_transcribe_with_mock_model(monkeypatch, tmp_path):
    from trami import asr

    class W:
        def __init__(self, s, e, w):
            self.start, self.end, self.word = s, e, w

    class S:
        def __init__(self, s, e, text, words):
            self.start, self.end, self.text, self.words, self.no_speech_prob = s, e, text, words, 0.1

    class Info:
        duration = 5.0
        language = "zh"

    class FakeModel:
        def transcribe(self, *a, **k):
            assert k["language"] == "zh" and k["word_timestamps"]
            ws = [W(0.5 + i * 0.3, 0.8 + i * 0.3, ch) for i, ch in enumerate("這是測試。我們走吧！")]
            return iter([S(0.5, 3.5, "這是測試。我們走吧！", ws), S(4, 4.5, "字幕由Amara.org社区提供", [])]), Info()

    monkeypatch.setattr(asr, "get_model", lambda *a, **k: FakeModel())
    segs = asr.transcribe(tmp_path / "x.wav", {"asr_model": "tiny"}, "zh")
    assert [s.text for s in segs] == ["这是测试。", "我们走吧！"]  # đã chuyển Giản thể, lọc ảo giác


# ------------------------------------------------------------------ dịch

def test_glossary_parse():
    from trami.translate import parse_glossary

    g = parse_glossary("李逍遥=Lý Tiêu Dao\n# bỏ qua\n蜀山 -> Thục Sơn\n")
    assert g == {"李逍遥": "Lý Tiêu Dao", "蜀山": "Thục Sơn"}


def test_llm_translate_batches_and_repair(monkeypatch):
    from trami import translate as tr

    calls = []

    def fake_chat(self, system, user, max_tokens=8000, temperature=0.3):
        calls.append(user)
        if "KỊCH BẢN" in user:
            return json.dumps({"genre": "cổ trang", "terms": {"李逍遥": "Lý Tiêu Dao"}, "relations": ""})
        payload = json.loads(user[user.rindex("{"):])
        out = {k: "VI(" + re.sub(r"[㐀-鿿]", "x", v) + ")" for k, v in payload.items()}
        if len(payload) > 3:  # lần đầu thiếu 1 dòng -> phải tự bù
            out.pop(str(len(payload)))
        return json.dumps(out, ensure_ascii=False)

    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    t = tr.Translator({"translate_engine": "openai", "openai_api_key": "x", "translate_batch": 5}, style="co_trang")
    segs = [Segment(i, i + 1, f"李逍遥说第{i}句") for i in range(12)]
    t.translate_segments(segs)
    assert all(s.trans.startswith("VI(") for s in segs)
    assert t.glossary.get("李逍遥") == "Lý Tiêu Dao"
    assert any("BẢNG THUẬT NGỮ" in c and "Lý Tiêu Dao" in c for c in calls)
    # cache: dịch lại không gọi API
    n = len(calls)
    t.translate_lines([s.text for s in segs])
    assert len(calls) == n


def test_llm_retries_untranslated_and_does_not_cache_empty(monkeypatch):
    """AI chép lại câu gốc (còn chữ Hán) hoặc bỏ trống -> hỏi lại; câu lỗi không bị nhớ rỗng mãi."""
    from trami import translate as tr

    calls = []

    def fake_chat(self, system, user, max_tokens=8000, temperature=0.3):
        calls.append(user)
        payload = json.loads(user[user.rindex("{"):])
        out = {k: f"vi{k}" for k in payload}
        if len(calls) == 1:
            out["2"] = "放开我放开我"   # lần đầu: chép nguyên câu gốc
        return json.dumps(out, ensure_ascii=False)

    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    st = {"translate_engine": "openai", "openai_api_key": "x", "dub_fit": False}
    t = tr.Translator(st)
    segs = [Segment(0, 1, "不要再说了"), Segment(1, 2, "放开我放开我"), Segment(2, 3, "不要啊")]
    t.translate_segments(segs, auto_terms=False)
    assert all(s.trans and not tr.untranslated(s.trans) for s in segs)
    assert len(calls) == 2

    # API lỗi hẳn (Google cũng lỗi) -> câu rỗng KHÔNG được nhớ, lần sau dịch lại được
    cache: dict = {}
    t2 = tr.Translator(st, cache=cache)
    monkeypatch.setattr(tr.LLMClient, "chat", lambda *a, **k: "không phải json")
    monkeypatch.setattr(tr.Translator, "_google", lambda self, lines: [""] * len(lines))
    assert t2.translate_lines(["你好"]) == [""]
    assert not any(v == "" for v in cache.values())
    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    assert t2.translate_lines(["你好"])[0]


def test_google_translate_mock(monkeypatch):
    from trami import translate as tr

    monkeypatch.setattr(tr.Translator, "_gtx", staticmethod(lambda text: "\n".join(f"vi:{l}" for l in text.split("\n"))))
    t = tr.Translator({"translate_engine": "google", "glossary": "李逍遥=Lý Tiêu Dao"})
    res = t.translate_lines(["你好。", "李逍遥来了"])
    assert res[0] == "Vi:你好。".rstrip(".") or res[0].startswith("Vi:")
    assert "Lý Tiêu Dao" in res[1]


def test_extract_json_variants():
    from trami.translate import _extract_json

    assert _extract_json('```json\n{"1": "a"}\n```') == {"1": "a"}
    assert _extract_json('Kết quả: {"1": "a", "2": "b"} xong') == {"1": "a", "2": "b"}


# ------------------------------------------------------------------ video / TTS

def test_probe(sample_video):
    info = ff.probe(sample_video)
    assert info.width == 640 and info.height == 360 and info.has_audio and 5.5 < info.duration < 6.5


def test_render_full_spec(sample_video, tmp_path):
    from trami.config import load_settings
    from trami.video import build_and_render

    spec = {"trim": {"start": 0.5, "end": 5.5}, "cuts": [[2, 3]], "aspect": "9:16", "resolution": "480p",
            "hardsub": {"mode": "blur", "regions": [{"x": 0.1, "y": 0.78, "w": 0.8, "h": 0.15}]},
            "flip_h": True, "speed": 1.25, "zoom": 1.05, "color": {"saturation": 1.2},
            "text": {"content": "Vietsub TraMi", "position": "scroll"}, "fade": {"in": 0.3, "out": 0.3},
            "subtitles": {"burn": True, "which": "trans"}}
    segs = [Segment(1, 2, "你好", "Xin chào"), Segment(3.2, 4.5, "再见", "Tạm biệt")]
    out = build_and_render(sample_video, tmp_path / "o.mp4", spec, load_settings(), tmp_path / "w", segs)
    info = ff.probe(out)
    assert (info.width, info.height) == (480, 854)
    assert abs(info.duration - 4.0 / 1.25) < 0.3
    assert info.has_audio


@pytest.mark.parametrize("mode", ["box", "delogo", "inpaint"])
def test_render_hardsub_modes(sample_video, tmp_path, mode):
    from trami.config import load_settings
    from trami.video import build_and_render

    spec = {"hardsub": {"mode": mode, "regions": [{"x": 0.1, "y": 0.78, "w": 0.8, "h": 0.15}]},
            "trim": {"start": 0, "end": 2}}
    out = build_and_render(sample_video, tmp_path / f"{mode}.mp4", spec, load_settings(), tmp_path / "w", [])
    assert ff.probe(out).duration > 1.5


def test_inpaint_removes_text():
    import cv2

    from trami.inpaint import text_mask

    img = np.full((120, 600, 3), (40, 90, 160), np.uint8)
    cv2.putText(img, "SUBTITLE TEXT", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 9)
    cv2.putText(img, "SUBTITLE TEXT", (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3)
    m = text_mask(img, 5)
    white = (img.min(axis=2) > 200)
    assert (m[white] > 0).mean() > 0.95  # phủ gần hết nét chữ
    res = cv2.inpaint(img, m, 5, cv2.INPAINT_TELEA)
    assert (res.min(axis=2) > 200).sum() < white.sum() * 0.03


def _speech_like(sr: int, dur: float = 1.0, seed: int = 0) -> np.ndarray:
    """Âm thanh giả GIỐNG TIẾNG NÓI (nhiều hoạ âm, cao độ & độ to thay đổi, có chỗ nghỉ + phụ âm) – bộ đọc giả trong
    test phải trả loại này: sóng sin đơn bị TraMi coi là "tiếng bíp" (giọng AI hỏng) và đọc lại / bỏ câu."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * sr)) / sr
    f0 = 140 + 40 * np.sin(2 * np.pi * 1.3 * t)
    ph = 2 * np.pi * np.cumsum(f0) / sr
    voiced = sum((0.5 / k) * np.sin(k * ph) for k in range(1, 12))
    env = (np.sin(2 * np.pi * 3 * t) > -0.3).astype(float) * (0.5 + 0.5 * np.sin(2 * np.pi * 0.7 * t) ** 2)
    return (0.2 * voiced * env + 0.02 * rng.standard_normal(len(t)) * (1 - env)).astype(np.float32)


def _fake_audio(path, dur, freq=500):
    subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={dur}", str(path)], check=True)


def test_dub_assembly(monkeypatch, tmp_path):
    """Giả lập Edge TTS bằng file sine để kiểm tra ghép + co giãn thời gian."""
    from trami import tts

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            _fake_audio(p, 3.0 if "dài" in text else 0.8)
        return []

    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    segs = [Segment(0.5, 1.5, "a", "ngắn"), Segment(2.0, 3.0, "b", "câu rất dài"), Segment(4.0, 5.0, "c", "ngắn")]
    out = tts.synthesize_dub(segs, tmp_path / "dub.wav", 6.0, tmp_path / "t", max_speedup=1.6)
    a = tts._decode(out)
    assert abs(len(a) / tts.SR - 6.0) < 0.05
    # câu dài (3s) được nén vừa trước câu tiếp theo (4.0s), tận dụng khoảng lặng
    active = np.where(np.abs(a) > 0.05)[0] / tts.SR
    seg2 = active[(active > 1.9) & (active < 3.95)]
    assert seg2.max() > 3.5


def test_multi_voice_clone_and_skip_song(monkeypatch, tmp_path):
    """Mỗi người nói 1 giọng; 'clone' lấy mẫu giọng từ video; câu hát được bỏ qua."""
    from trami import tts

    calls = []

    class FakeVieneu:
        sr = 48000

        def ensure_started(self, progress=None, cancel=None):
            pass

        def synth(self, text, out, voice=None, ref_audio=None, **kw):
            calls.append((text, ref_audio, voice))
            return tts.write_wav(out, _speech_like(48000), 48000)

    edge_voices = []

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            edge_voices.append(voice)
            _fake_audio(p, 0.6)
        return []

    monkeypatch.setattr(tts, "vieneu_available", lambda: True)
    monkeypatch.setattr(tts, "_vieneu", lambda mode: FakeVieneu())
    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    ref = tmp_path / "vocals.wav"
    _fake_audio(ref, 12, 300)
    segs = [Segment(0.5, 3.0, "a", "Xin chào", "S1"), Segment(3.5, 6.0, "b", "Chào huynh", "S2"),
            Segment(6.5, 9.0, "c", "Câu thứ ba", "S1"), Segment(9.5, 11.5, "d", "La la la", "S3", {"song": True}),
            Segment(4.0, 4.5, "e", "Bỏ", "S2", {"nodub": True})]
    voices = {"S1": "clone", "S2": "vieneu:Ngọc Huyền", "S3": "edge:vi-VN-NamMinhNeural"}
    tts.synthesize_dub(segs, tmp_path / "dub.wav", 12, tmp_path / "t", voices=voices, ref_audio=ref)
    texts = [c[0] for c in calls]
    assert "Xin chào" in texts and "Chào huynh" in texts and "Câu thứ ba" in texts
    assert "La la la" not in texts and "Bỏ" not in texts and not edge_voices  # hát + nodub bị bỏ
    clone = [c for c in calls if c[0] == "Xin chào"][0]
    assert clone[1] and Path(clone[1]).exists()                              # có file mẫu giọng S1
    assert [c for c in calls if c[0] == "Chào huynh"][0][2] == "Ngọc Huyền"   # giọng dựng sẵn


def test_voice_list_and_auto_assign():
    from trami import tts

    v = tts.auto_assign(["S1", "S2", "S3"], {"tts_engine": "edge"}, {"S2": "edge:vi-VN-HoaiMyNeural"})
    assert v["S2"] == "edge:vi-VN-HoaiMyNeural" and v["S1"] != v["S2"]
    assert all(x.startswith("edge:") for x in v.values())


def test_project_and_pipeline_export(sample_video):
    from trami import pipeline
    from trami.project import Project

    p = Project.create("test")
    pipeline.attach_source(p, sample_video)
    assert p.data["info"]["width"] == 640
    p.segments = [Segment(0.5, 1.5, "你好", "Xin chào")]
    p.save()
    srt = pipeline.export_subs(Project(p.id), "srt", "trans")
    assert "Xin chào" in srt.read_text("utf-8-sig")
    out = pipeline.render(Project(p.id))
    assert out.exists() and ff.probe(out).duration > 5
    p.delete()


def test_diarize_cluster():
    from collections import Counter

    from trami.diarize import cluster

    rng = np.random.default_rng(1)
    centers = rng.normal(size=(3, 192))
    centers /= np.linalg.norm(centers, axis=1, keepdims=True)
    true = rng.choice(3, size=90, p=[.5, .3, .2])
    e = centers[true] + rng.normal(scale=0.045, size=(90, 192))
    e /= np.linalg.norm(e, axis=1, keepdims=True)
    for n in (0, 3):
        lab = cluster(e, 0.62, n)
        assert len(set(lab)) == 3
        purity = sum(Counter(true[lab == k]).most_common(1)[0][1] for k in set(lab)) / len(lab)
        assert purity > 0.97


def test_diarize_merges_ghost_speakers():
    """Vài câu gào / cười của 1 nhân vật lệch giọng thành cụm riêng -> gộp về nhân vật gần nhất;
    nhân vật thật nói ít nhưng giọng khác hẳn thì vẫn giữ riêng."""
    from trami.diarize import cluster

    rng = np.random.default_rng(2)
    c = rng.normal(size=(3, 192))
    c /= np.linalg.norm(c, axis=1, keepdims=True)

    def around(v, k, noise):
        e = v + rng.normal(scale=noise, size=(k, 192))
        return e / np.linalg.norm(e, axis=1, keepdims=True)

    u = rng.normal(size=192)
    u -= (u @ c[0]) * c[0]
    ghost = 0.53 * c[0] + np.sqrt(1 - 0.53 ** 2) * u / np.linalg.norm(u)   # giọng A biến dạng (gào, cười)
    e = np.vstack([around(c[0], 20, 0.04), around(c[1], 15, 0.04), around(ghost, 2, 0.02), around(c[2], 2, 0.02)])
    durs = np.r_[np.full(35, 2.0), [0.9, 1.0], [1.5, 1.4]]
    sim_ghost = float((e[35:37].mean(0) / np.linalg.norm(e[35:37].mean(0))) @ c[0])
    assert 0.45 < sim_ghost < 0.62                        # đủ khác để thành cụm riêng ở ngưỡng 0,62
    lab = cluster(e, 0.62, 0, durs)
    assert len(set(lab[35:37])) == 1 and lab[35] == lab[0]   # cụm ảo gộp về A
    assert lab[37] == lab[38] and lab[37] not in (lab[0], lab[20])   # người thứ 3 (giọng khác hẳn) giữ riêng
    assert len(set(lab)) == 3


def test_sensevoice_parsing_and_songs():
    from trami.asr_funasr import mark_songs, parse_sensevoice, split_long

    t, tags = parse_sensevoice("<|zh|><|NEUTRAL|><|BGM|><|withitn|>你好。我们走吧！")
    assert t == "你好。我们走吧！" and "BGM" in tags
    parts = split_long(0.0, 4.0, "你好。我们走吧！今天天气很好，我们去爬山吧。", 10)
    assert len(parts) >= 3 and abs(parts[-1][1] - 4.0) < 1e-6
    s1 = Segment(0, 4, "啊啊啊啊", extra={"tags": ["BGM"]})       # chậm + có nhạc -> hát
    s2 = Segment(0, 2, "我们今天去哪里吃饭呢", extra={"tags": ["BGM"]})  # nói nhanh -> không phải hát
    s3 = Segment(0, 1, "唱", extra={"tags": ["Sing"]})
    mark_songs([s1, s2, s3])
    assert s1.extra.get("song") and not s2.extra.get("song") and s3.extra.get("song")


def test_build_reference(tmp_path):
    from trami import tts

    ref = tmp_path / "v.wav"
    _fake_audio(ref, 20, 300)
    segs = [Segment(1, 4, "a", speaker="S1"), Segment(5, 9, "b", speaker="S1"), Segment(10, 12, "c", speaker="S2")]
    out = tts.build_reference(ref, segs, "S1", tmp_path / "r.wav")
    d = ff.probe(out).duration
    assert out and 5 <= d <= 12.5


def test_render_with_separated_stems(sample_video, tmp_path):
    """Lồng tiếng + nhạc nền đã tách + giữ giọng hát gốc ở đoạn hát, kèm cắt đoạn và đổi tốc độ."""
    from trami.config import load_settings
    from trami.video import build_and_render

    for name, f in (("bg.wav", 200), ("voc.wav", 400), ("dub.wav", 600)):
        _fake_audio(tmp_path / name, 6, f)
    spec = {"trim": {"start": 0.5, "end": 5.5}, "cuts": [[2, 3]], "speed": 1.25,
            "dub": {"enabled": True, "wav": str(tmp_path / "dub.wav"), "bg_wav": str(tmp_path / "bg.wav"),
                    "vocals_wav": str(tmp_path / "voc.wav"), "song_spans": [[3.5, 4.5]]}}
    out = build_and_render(sample_video, tmp_path / "o.mp4", spec, load_settings(), tmp_path / "w", [])
    info = ff.probe(out)
    assert info.has_audio and abs(info.duration - 4.0 / 1.25) < 0.3


def test_lama_inpaint_if_model_present():
    from trami import lama

    if not lama.available() or not lama.LAMA_FILE.exists():
        pytest.skip("Chưa có mô hình LaMa")
    import cv2

    img = np.full((120, 400, 3), (40, 140, 200), np.uint8)
    cv2.putText(img, "TEXT", (60, 85), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (255, 255, 255), 8)
    mask = (img.min(axis=2) > 200).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8))
    res = lama.inpaint(img, mask)
    assert res.shape == img.shape
    assert (res.min(axis=2) > 200).sum() < 20                          # chữ đã bị xoá
    assert np.abs(res[mask > 0].astype(int) - [40, 140, 200]).mean() < 25  # nền khớp màu


def test_gui_smoke(sample_video, tmp_path):
    """Mở cửa sổ chính (offscreen), mở dự án, chuyển qua tất cả các tab."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:  # thiếu thư viện đồ hoạ hệ thống
        pytest.skip(str(e))
    from trami import pipeline
    from trami.gui import theme
    from trami.gui.main import MainWindow
    from trami.gui.widgets import set_combo
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    p = Project.create("gui")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(0.5, 1.5, "你好", "Xin chào", "S1"), Segment(2, 3, "朋友", "Bạn", "S2")]
    p.save()
    win = MainWindow()
    win.show()
    win.open_project(p.id)
    pp = win.project_page
    assert pp.model.rowCount() == 2 and pp.spk_table.rowCount() == 2
    for i in range(7):
        pp.tabs.set_current(i)
        app.processEvents()
    pp.e_speed.set(1.5)
    pp._save_edit_now()
    assert Project(p.id).edit["speed"] == 1.5
    pp.model.setData(pp.model.index(0, 5), "Xin chào bạn")
    pp.flush()
    assert Project(p.id).segments[0].trans == "Xin chào bạn"
    # đổi "Dịch sang" -> lưu vào dự án, nhãn cột / lựa chọn phụ đề / danh sách giọng theo tiếng mới
    from PySide6.QtCore import Qt as _Qt

    pp.a_target.setCurrentIndex(pp.a_target.findData("en"))
    app.processEvents()
    assert Project(p.id).data["target_lang"] == "en"
    assert pp.model.headerData(5, _Qt.Orientation.Horizontal) == "Tiếng Anh"
    assert pp.a_which.itemText(pp.a_which.findData("trans")) == "Chỉ tiếng Anh"
    assert all(v["id"].startswith(("edge:en-", "omni")) for v in pp._voices)
    pp.a_target.setCurrentIndex(pp.a_target.findData("vi"))
    app.processEvents()
    assert pp.model.headerData(5, _Qt.Orientation.Horizontal) == "Tiếng Việt"
    # tab Xoá phụ đề: khoanh logo cố định riêng với vùng phụ đề; làm mờ bo góc; tab Kiểu chữ: nền bo góc, độ dài dòng
    pp._regions_edited([{"x": 0.05, "y": 0.8, "w": 0.9, "h": 0.15}])
    pp.hs_target.set_current(1)
    assert pp.player.view.hs.target == "logos" and pp.hs_editor.regions == []
    pp._regions_edited([{"x": 0.8, "y": 0.02, "w": 0.15, "h": 0.08}])
    pp.hs_round.set(50)
    pp.st_box.setChecked(True)
    set_combo(pp.st_bfixed, "block")
    pp.st_bradius.set(30)
    pp.st_lwidth.set(80)
    pp._save_edit_now()
    e = Project(p.id).edit
    assert len(e["hardsub"]["regions"]) == 1 and len(e["hardsub"]["logos"]) == 1 and e["hardsub"]["round"] == 0.5
    sty = e["subtitles"]["style"]
    assert sty["box_block"] and sty["box_radius"] == 0.3 and sty["line_width"] == 0.8
    pp.hs_target.set_current(0)
    assert len(pp.hs_editor.regions) == 1 and len(pp.hs_editor.extra) == 1
    for k in ("batch", "concat", "settings", "home"):
        win.show_page(k)
        app.processEvents()
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().waitForDone(15000)  # chờ các tác vụ nền (chụp khung hình…) xong
    win.close()
    p.delete()


def test_subtitle_auto_position_matches_old_region(sample_video):
    """Vị trí 'auto': câu Việt canh vào đúng dòng phụ đề Trung cũ, cỡ chữ theo chữ cũ."""
    from trami import pipeline
    from trami.project import Project

    p = Project.create("pos")
    pipeline.attach_source(p, sample_video)
    p.data["ocr_region"] = {"x": 0.1, "y": 0.63, "w": 0.8, "h": 0.1067}
    p.save()
    st = pipeline.effective_style(Project(p.id), {"position": "auto", "size": 0})
    assert st["position"] == "bottom" and 0.27 < st["margin_v_rel"] < 0.31   # đáy chữ ≈ 0.71 khung hình
    assert st["size"] > 360 * 0.06                                           # chữ to ngang chữ Trung cũ
    # khung hình bị đổi (9:16) -> không suy ra được, quay về mặc định sát đáy
    e = p.edit
    e["aspect"] = "9:16"
    p.data["edit"] = e
    p.save()
    assert "margin_v_rel" not in pipeline.effective_style(Project(p.id), {"position": "auto"})
    # người dùng chọn vị trí cố định -> giữ nguyên
    assert pipeline.effective_style(Project(p.id), {"position": "top"})["position"] == "top"
    p.delete()


def test_settings_store_only_changes_and_migrate(tmp_path, monkeypatch):
    import json

    from trami import config

    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    old = json.loads(json.dumps(config.DEFAULT_SETTINGS))       # kiểu lưu của bản 1.x: lưu cứng mọi thứ
    old["sub_style"]["position"] = "bottom"
    old["deepseek_api_key"] = "sk-abc"
    f.write_text(json.dumps(old), "utf-8")
    s = config.load_settings()
    assert s["sub_style"]["position"] == "auto" and s["deepseek_api_key"] == "sk-abc"
    config.save_settings({"translate_engine": "deepseek"})
    stored = json.loads(f.read_text("utf-8"))
    assert stored["translate_engine"] == "deepseek" and stored["deepseek_api_key"] == "sk-abc"
    assert "asr_model" not in stored                              # giá trị mặc định không bị lưu cứng


def test_ai_connection_error_is_reported_not_silently_google(monkeypatch):
    from trami import translate as tr

    def down(self, *a, **k):
        raise tr.ApiError("mất kết nối")

    google_called = []
    monkeypatch.setattr(tr.LLMClient, "chat", down)
    monkeypatch.setattr(tr.Translator, "_google", lambda self, lines: google_called.append(1) or ["x"] * len(lines))
    t = tr.Translator({"translate_engine": "openai", "openai_api_key": "k"})
    with pytest.raises(tr.ApiError):
        t.translate_segments([Segment(0, 1, "你好")])
    assert not google_called


def test_ai_checks_do_not_import_heavy_packages():
    """Kiểm tra 'đã cài chưa' phải tức thì (import funasr/torch có thể mất cả phút -> treo giao diện)."""
    import time

    from trami import asr_funasr, diarize, lama, separate, tts

    t = time.time()
    for f in (separate.available, asr_funasr.available, diarize.available, lama.available, tts.vieneu_available):
        f()
    tts.list_voices({})
    assert time.time() - t < 1.0


def test_sensevoice_broken_library_falls_back_to_whisper(sample_video, monkeypatch):
    """torchaudio lệch phiên bản với torch -> SenseVoice lỗi OSError -> tự dùng Whisper, không dừng quy trình."""
    from trami import asr, asr_funasr, pipeline
    from trami.project import Project

    def broken(*a, **k):
        raise OSError("Could not load this library: libtorchaudio.pyd")

    monkeypatch.setattr(asr_funasr, "available", lambda: True)
    monkeypatch.setattr(asr_funasr, "transcribe_sensevoice", broken)
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: [Segment(0.5, 2.0, "你好")])
    p = Project.create("fallback")
    pipeline.attach_source(p, sample_video)
    st = {"asr_engine": "sensevoice", "sep_before_asr": False, "diarize": False, "asr_max_chars": 22}
    n = pipeline.recognize(Project(p.id), "asr", "zh", settings=st)
    pr = Project(p.id)
    assert n == 1 and pr.data["asr_engine_used"] == "whisper" and "Whisper" in pr.data["asr_warning"]
    p.delete()


def test_dub_versions_use_and_delete(tmp_path, monkeypatch):
    """Mỗi lần lồng tiếng là 1 bản; chọn bản nào thì bản đó dùng khi xuất; xoá bản đang dùng -> chuyển bản khác."""
    from trami import pipeline, tts
    from trami.project import Project

    p = Project.create("dubver")
    p.data["info"] = {"duration": 5}
    p.segments = [Segment(0.5, 2.0, "a", "Xin chào", "S1")]
    p.save()

    def fake_synth(segs, out, *a, stats=None, **k):
        tts.write_wav(out, np.zeros(1000, np.float32))
        if stats is not None:
            stats.update({"placed": 1, "total": 1})
        return Path(out)
    monkeypatch.setattr(tts, "synthesize_dub", fake_synth)
    times = iter(["20260101_000001", "20260101_000002"])
    real = pipeline.time.strftime
    monkeypatch.setattr(pipeline.time, "strftime", lambda f, *a: next(times) if f == "%Y%m%d_%H%M%S" else real(f, *a))
    w1 = pipeline.dub(p, "edge:vi-VN-HoaiMyNeural", engine="edge")
    w2 = pipeline.dub(p, "edge:vi-VN-NamMinhNeural", engine="edge")
    vers = pipeline.dub_versions(p)
    assert [v["wav"] for v in vers] == [str(w2), str(w1)] and "NamMinh" in vers[0]["voices"]
    assert p.edit["dub"]["wav"] == str(w2)
    pipeline.use_dub_version(p, str(w1))
    assert p.edit["dub"]["wav"] == str(w1) and p.edit["dub"]["enabled"]
    pipeline.delete_dub_version(p, str(w1))
    assert not w1.exists() and p.edit["dub"]["wav"] == str(w2) and len(pipeline.dub_versions(p)) == 1
    pipeline.delete_dub_version(p, str(w2))
    assert not p.edit["dub"]["enabled"] and pipeline.dub_versions(p) == []


def test_redub_selected_lines_in_place(monkeypatch, tmp_path):
    """Sửa / đổi giọng 1–2 câu: chỉ câu đó được đọc lại, bản lồng đang dùng được cập nhật tại chỗ (không thêm bản mới),
    câu người dùng tự sửa không bị AI rút gọn."""
    from trami import pipeline, tts
    from trami.project import Project

    spoken = []

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            spoken.append((i, text, voice))
            _fake_audio(p, 0.6, 300 + 100 * i)
        return []

    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    p = Project.create("redub")
    p.data["info"] = {"duration": 8}
    p.segments = [Segment(0.5, 1.5, "a", "Một", "S1"), Segment(2.5, 3.5, "b", "Hai", "S1"),
                  Segment(4.5, 5.5, "c", "Ba", "S2")]
    p.save()
    st = {"translate_engine": "google", "tts_voice": "vi-VN-HoaiMyNeural"}
    w1 = pipeline.dub(p, None, settings=st, engine="edge")
    assert len(spoken) == 3
    spoken.clear()
    # người dùng sửa câu 2 + đổi giọng riêng câu 3
    segs = p.segments
    segs[1].trans, segs[1].extra["trans_user"] = "Hai sửa", True
    segs[2].extra["voice"] = "edge:vi-VN-NamMinhNeural"
    p.segments = segs
    p.save()
    mtime = w1.stat().st_mtime_ns
    w2 = pipeline.redub(p, [1, 2], settings=st, engine="edge")
    assert w2 == w1 and w1.stat().st_mtime_ns != mtime          # ghi đè bản đang dùng
    vers = pipeline.dub_versions(p)
    assert len(vers) == 1 and vers[0]["edits"] == 1 and vers[0]["created"]   # sửa thẳng, đếm số lần sửa
    assert sorted(i for i, _, _ in spoken) == [1, 2]                # câu 1 dùng lại giọng đã đọc
    assert dict((i, v) for i, _, v in spoken)[2] == "vi-VN-NamMinhNeural"
    assert p.edit["dub"]["wav"] == str(w1) and p.edit["dub"]["enabled"]
    # tạo lại giọng (không sửa gì) -> vẫn đọc lại đúng câu đã chọn (lần đọc mới)
    spoken.clear()
    pipeline.redub(p, [0], settings=st, engine="edge")
    assert [i for i, _, _ in spoken] == [0] and p.segments[0].extra["take"] == 1


def test_dub_clip_moved_on_timeline(monkeypatch):
    """Mỗi câu là 1 đoạn giọng riêng: kéo đoạn giọng trên timeline -> xếp lại bản lồng đúng chỗ mới, KHÔNG đọc lại câu
    nào; đặt lại -> về đúng lúc nhân vật nói; có sóng âm cho timeline."""
    from trami import pipeline, tts
    from trami.project import Project

    spoken = []

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            spoken.append(i)
            _fake_audio(p, 0.6, 300 + 100 * i)
        return []

    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    p = Project.create("dubmove")
    p.data["info"] = {"duration": 8}
    p.segments = [Segment(0.5, 1.5, "a", "Một", "S1"), Segment(2.5, 3.5, "b", "Hai", "S1"),
                  Segment(5.0, 6.0, "c", "Ba", "S2")]
    p.save()
    st = {"translate_engine": "google", "tts_voice": "vi-VN-HoaiMyNeural"}
    wav = pipeline.dub(p, None, settings=st, engine="edge")
    lay = {int(k): a for a, b, k, f in p.data["dub_layout"]}
    assert abs(lay[1] - 2.5) < 0.2 and len(spoken) == 3
    env, hop = pipeline.dub_wave(p)
    assert Path(env).exists() and hop == 0.05 and len(np.load(env)) > 100
    spoken.clear()
    segs = p.segments
    segs[1].extra["dub_shift"] = 0.8
    p.segments = segs
    p.save()
    assert pipeline.relayout(p, st) == wav
    lay = {int(k): a for a, b, k, f in p.data["dub_layout"]}
    assert abs(lay[1] - 3.3) < 0.05 and abs(lay[0] - 0.5) < 0.2 and spoken == []
    segs = p.segments
    segs[1].extra.pop("dub_shift")
    p.segments = segs
    p.save()
    pipeline.relayout(p, st)
    assert abs({int(k): a for a, b, k, f in p.data["dub_layout"]}[1] - 2.5) < 0.2 and spoken == []
    assert "wave" not in (p.data.get("dub_stats") or {})


def test_qwen3_asr_fallback_and_helpers(sample_video, monkeypatch):
    """Qwen3-ASR: chưa cài transformers đủ mới -> tự dùng SenseVoice + báo cách cài; lỗi khi chạy -> SenseVoice;
    chạy được -> khúc có mốc từng chữ, refine tách câu trọn nghĩa; bảng thuật ngữ -> gợi ý từ cho bộ nghe."""
    from trami import asr_funasr, asr_qwen, pipeline
    from trami.project import Project

    monkeypatch.setattr("trami.config.installed", lambda *a: True)
    monkeypatch.setattr(asr_qwen, "transformers_version", lambda: (4, 57, 6))
    assert not asr_qwen.available() and "5.13" in asr_qwen.why_unavailable()
    monkeypatch.setattr(asr_funasr, "available", lambda: True)
    monkeypatch.setattr(asr_funasr, "transcribe_sensevoice", lambda *a, **k: [Segment(0.5, 2.0, "你好。")])
    p = Project.create("qwen")
    pipeline.attach_source(p, sample_video)
    st = {"asr_engine": "qwen3", "sep_before_asr": False, "diarize": False, "align_words": False}
    pipeline.recognize(Project(p.id), "asr", "zh", settings=st)
    pr = Project(p.id)
    assert pr.data["asr_engine_used"] == "sensevoice" and "transformers" in pr.data["asr_warning"]
    # đủ điều kiện nhưng tiến trình lỗi (VD hết bộ nhớ GPU) -> SenseVoice, không dừng
    monkeypatch.setattr(asr_qwen, "transformers_version", lambda: (5, 20, 1))

    def boom(*a, **k):
        raise RuntimeError("CUDA out of memory")
    monkeypatch.setattr(asr_qwen, "transcribe_qwen", boom)
    pipeline.recognize(Project(p.id), "asr", "zh", settings=st)
    pr = Project(p.id)
    assert pr.data["asr_engine_used"] == "sensevoice" and "out of memory" in pr.data["asr_warning"]
    # chạy được: 1 khúc có mốc từng chữ -> tách thành câu trọn nghĩa, không còn cảnh báo
    text = "好了，不要闹了。待会儿变回来，把你打成猫饼。"
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    words = [[c, 0.3 * k, 0.3 * k + 0.25] for k, c in enumerate(chars)]
    seg = asr_qwen._segment(1.0, 6.0, {"text": text, "words": words}, True)
    assert seg and len(seg.extra["ct"]) == len(chars) and seg.start >= 1.0
    monkeypatch.setattr(asr_qwen, "transcribe_qwen", lambda *a, **k: [seg])
    st["align_words"] = True
    monkeypatch.setattr(pipeline, "use_qwen_align", lambda *a: False)
    pipeline.recognize(Project(p.id), "asr", "zh", settings=dict(st, align_fa=False))
    pr = Project(p.id)
    assert pr.data["asr_engine_used"] == "qwen3" and not pr.data.get("asr_warning")
    assert [s.text for s in pr.segments] == ["好了，不要闹了。", "待会儿变回来，把你打成猫饼。"]
    assert all("ct" not in s.extra and "chunked" not in s.extra for s in pr.segments)
    assert asr_qwen.hotwords({"glossary": "林动=Lâm Động\nabc=xyz\n青檀=Thanh Đàn"}) == "热词：林动、青檀"
    p.delete()


def test_shorten_selected_lines(monkeypatch):
    """AI rút gọn riêng các câu đã chọn, giữ bản dài để xem lại; Google không rút gọn được -> báo rõ."""
    from trami import pipeline, translate
    from trami.project import Project

    p = Project.create("shorten")
    p.segments = [Segment(0, 1.0, "你好", "Xin chào bạn thân mến của tôi ơi"), Segment(1.2, 2.2, "走", "Đi thôi")]
    p.save()
    with pytest.raises(ValueError, match="AI"):
        pipeline.shorten_lines(p, [0], {"translate_engine": "google"})
    asked = []
    monkeypatch.setattr(translate.Translator, "shorten",
                        lambda self, items: asked.extend(items) or ["Chào bạn" for _ in items])
    r = pipeline.shorten_lines(p, [0, 1], {"translate_engine": "openai", "openai_api_key": "x"})
    segs = p.segments
    assert r["changed"] == [0] and segs[0].trans == "Chào bạn" and segs[0].extra["trans_long"].startswith("Xin chào")
    assert r["fit"] == [1] and segs[1].trans == "Đi thôi" and len(asked) == 1     # câu ngắn đã vừa: không gửi AI
    assert pipeline.unshorten_lines(p, [0, 1]) == [0]
    assert p.segments[0].trans.startswith("Xin chào") and "trans_long" not in p.segments[0].extra


def test_shorten_only_lines_that_do_not_fit():
    """Đã lồng tiếng: chỉ rút gọn câu ⚠ đỏ / ⚡ phải đọc nhanh hơn ×1,15; số tiếng mục tiêu theo giọng đọc thật."""
    from trami import pipeline

    ok = Segment(0, 2, "a", "một hai ba bốn năm sáu", extra={"fit": "ok", "fit_speed": 1.0})
    fast_ok = Segment(0, 2, "a", "một hai ba bốn năm sáu", extra={"fit": "fast", "fit_speed": 1.12})
    fast = Segment(0, 2, "a", "một hai ba bốn năm sáu bảy tám chín mười",
                   extra={"fit": "fast", "fit_speed": 1.3, "fit_need": 2.6, "fit_have": 2.0})
    long_ = Segment(0, 2, "a", "một hai ba bốn năm sáu bảy tám chín mười",
                    extra={"fit": "long", "fit_speed": 1.3, "fit_need": 4.0, "fit_have": 2.0})
    assert pipeline.shorten_target(ok, 2.0) is None and pipeline.shorten_target(fast_ok, 2.0) is None
    assert pipeline.shorten_target(fast, 2.0) == 8          # 10 tiếng × 2,0×1,1/2,6 × 0,95
    assert pipeline.shorten_target(long_, 2.0) == 5         # 10 tiếng × 2,0×1,1/4,0 × 0,95
    pushed = Segment(0, 2, "a", "một hai ba", extra={"fit": "long", "fit_speed": 1.3, "fit_need": 0.8,
                                                      "fit_have": 2.0})   # chỉ bị câu trước đẩy trễ
    early_ok = Segment(0, 2, "a", "một hai ba bốn năm sáu bảy tám chín mười",
                       extra={"fit": "ok", "fit_speed": 1.0, "fit_need": 3.0, "fit_have": 2.0})   # đọc sớm mà vừa
    assert pipeline.shorten_target(pushed, 2.0) is None and pipeline.shorten_target(early_ok, 2.0) is None
    assert pipeline.needs_shorten(long_) and not pipeline.needs_shorten(pushed)
    fresh = Segment(0, 1, "a", "một hai ba bốn năm sáu bảy tám chín mười")   # chưa lồng: ước theo chỗ trống
    assert pipeline.shorten_target(fresh, 1.0) is not None and pipeline.shorten_target(fresh, 4.0) is None


def test_import_stems_from_other_app(tmp_path):
    """Nạp giọng tách bằng phần mềm khác: thiếu nhạc nền -> nhạc nền = tiếng gốc − giọng; độ dài khớp video."""
    from trami import pipeline
    from trami.project import Project

    src, voc = tmp_path / "v.wav", tmp_path / "voc.wav"
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=900:duration=4", "-filter_complex", "amix=inputs=2:normalize=0",
                    "-ac", "2", str(src)], check=True)
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=900:duration=3.5",
                    "-ac", "2", str(voc)], check=True)
    p = Project.create("stems")
    pipeline.attach_source(p, src)
    v, i = pipeline.import_stems(p, voc)
    assert pipeline.stems(p) == {"vocals": str(v), "instrumental": str(i)}
    assert abs(ff.probe(v).duration - 4.0) < 0.1 and abs(ff.probe(i).duration - 4.0) < 0.1
    import wave

    with wave.open(str(i)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2)[:, 0].astype(np.float32)
    a = a[44100:44100 * 3]
    sp = np.abs(np.fft.rfft(a))
    f = np.fft.rfftfreq(len(a), 1 / 44100)
    assert sp[np.abs(f - 300) < 5].max() > 10 * sp[np.abs(f - 900) < 5].max()   # giọng (900 Hz) đã bị trừ đi


def test_redub_after_srt_import_with_edge_403(monkeypatch):
    """Tình huống người dùng: nạp SRT mới (mọi câu đổi lời), tích 2 câu, bấm Tạo lại giọng nhưng Edge trả 403.
    -> Chỉ 2 câu đã tích được đọc lại; đọc lỗi thì giữ giọng cũ, KHÔNG làm hỏng cả bản lồng; báo lệnh cập nhật edge-tts."""
    import edge_tts

    from trami import pipeline, tts
    from trami.project import Project

    calls = []

    class Comm:
        ok = True

        def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
            self.text = text

        async def save(self, out):
            calls.append(self.text)
            if not Comm.ok:
                raise RuntimeError("403, message='Invalid response status', url='wss://speech.platform.bing.com'")
            _fake_audio(out, 0.6, 400)

    monkeypatch.setattr(edge_tts, "Communicate", Comm)
    monkeypatch.setattr(tts.asyncio, "sleep", lambda *_a, **_k: _noop())
    p = Project.create("srt403")
    p.data["info"] = {"duration": 8}
    p.segments = [Segment(0.5 + 2 * i, 1.5 + 2 * i, "x", f"Câu cũ {i}", "S1") for i in range(4)]
    p.save()
    st = {"translate_engine": "google", "tts_voice": "vi-VN-HoaiMyNeural"}
    wav = pipeline.dub(p, None, settings=st, engine="edge")
    segs = p.segments
    changed = pipeline.apply_translation(segs, [Segment(0.5 + 2 * i, 1.5 + 2 * i, f"Câu mới {i}") for i in range(4)])
    assert changed == [0, 1, 2, 3]
    p.segments = segs
    p.save()
    # Edge hỏng (403)
    calls.clear()
    Comm.ok = False
    out = pipeline.redub(p, [1, 2], settings=st, engine="edge")
    assert out == wav and set(calls) <= {"Câu mới 1", "Câu mới 2"}          # không đọc lại câu không tích
    sts = p.data["dub_stats"]
    assert sts["placed"] == 4 and sts["kept_old"] == 2 and sts["redone"] == 0
    assert "edge-tts>=7.3.1" in sts["error_samples"][0]
    # Edge chạy lại được -> chỉ 2 câu đã tích được đọc lời mới
    calls.clear()
    Comm.ok = True
    pipeline.redub(p, [1, 2], settings=st, engine="edge")
    assert sorted(calls) == ["Câu mới 1", "Câu mới 2"] and p.data["dub_stats"]["redone"] == 2
    # bản lồng đầy đủ thì đọc nốt các câu đổi lời còn lại
    calls.clear()
    pipeline.dub(p, None, settings=st, engine="edge")
    assert sorted(calls) == ["Câu mới 0", "Câu mới 3"]


async def _noop():
    return None


def test_apply_translation_from_srt():
    """Nạp SRT đã dịch ở phần mềm khác: giữ câu gốc + mốc; cùng số câu -> theo thứ tự; khác -> theo thời gian."""
    from trami import pipeline

    segs = [Segment(1.0, 2.0, "你好", ""), Segment(2.5, 4.0, "我们走吧", ""), Segment(5.0, 6.0, "不要啊", "")]
    cues = [Segment(1.05, 2.0, "Xin chào"), Segment(2.5, 4.0, "我们走吧\nChúng ta đi thôi"), Segment(5.0, 6.1, "Đừng mà")]
    assert pipeline.apply_translation(segs, cues) == [0, 1, 2]
    assert [s.trans for s in segs] == ["Xin chào", "Chúng ta đi thôi", "Đừng mà"]
    assert [s.text for s in segs] == ["你好", "我们走吧", "不要啊"] and segs[0].extra["trans_user"]
    # phần mềm khác gộp / tách câu -> ghép theo thời gian
    segs2 = [Segment(1.0, 2.0, "你好", ""), Segment(2.5, 4.0, "我们走吧", "")]
    cues2 = [Segment(0.9, 2.1, "Xin chào"), Segment(2.4, 3.2, "Chúng ta"), Segment(3.2, 4.1, "đi thôi")]
    assert pipeline.apply_translation(segs2, cues2) == [0, 1]
    assert pipeline.apply_translation(segs2, cues2) == []      # nạp lại y hệt -> không câu nào đổi
    assert [s.trans for s in segs2] == ["Xin chào", "Chúng ta đi thôi"]


def test_auto_no_translate_exports_source_srt(monkeypatch):
    """Công cụ dịch 'Không dịch': chỉ nghe + tách câu, xuất SRT gốc, không dịch / không ghép video."""
    from trami import pipeline
    from trami.project import Project

    p = Project.create("notr")
    p.data["info"] = {"duration": 5, "has_video": True}
    p.segments = [Segment(0.5, 2.0, "你好", "")]
    p.save()
    with pytest.raises(ValueError, match="Không dịch"):   # bấm "Dịch" khi đang chọn Không dịch -> báo rõ
        pipeline.translate(p, pipeline.NO_TRANSLATE)
    monkeypatch.setattr(pipeline, "translate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("không được dịch")))
    monkeypatch.setattr(pipeline, "render", lambda *a, **k: (_ for _ in ()).throw(AssertionError("không ghép video")))
    res = pipeline.auto(p, {"engine": pipeline.NO_TRANSLATE, "dub": True})
    assert res["srt"].endswith(".zh.srt") and "你好" in Path(res["srt"]).read_text("utf-8")
    assert "không dịch" in res["message"]
    # tab Tự động (render False): dịch xong DỪNG trước bước ghép video, vẫn lưu cách xuất + SRT
    monkeypatch.setattr(pipeline, "translate", lambda q, *a, **k: setattr(q, "segments", [
        Segment(0.5, 2.0, "你好", "Xin chào")]) or q.save())
    res = pipeline.auto(p, {"engine": "google", "render": False, "hardsub_mode": "none", "burn": True})
    assert "video" not in res and res["srt"].endswith(".srt") and "chưa xuất video" in res["message"]
    assert Project(p.id).edit["subtitles"]["burn"] is True


def test_omnivoice_samples_and_dub(monkeypatch, tmp_path):
    """Giọng mẫu OmniVoice: thêm file ghi âm (cắt còn ≤10 giây), đọc theo giọng mẫu kèm lời mẫu; nhái nhân vật gốc."""
    from trami import tts, voices

    src = tmp_path / "rec.wav"
    _fake_audio(src, 14, 300)
    v = voices.add_sample(src, "Giọng A", "Xin chào các bạn")
    assert v["dur"] <= 10.1 and voices.path_of(v).exists()
    calls = []

    class FakeOmni:
        sr = 24000

        def ensure_started(self, progress=None, cancel=None):
            pass

        def synth(self, text, out, voice=None, ref_audio=None, ref_text=None, **kw):
            calls.append((text, Path(ref_audio).name if ref_audio else None, ref_text))
            return tts.write_wav(out, _speech_like(24000), 24000)

    monkeypatch.setattr(tts, "omnivoice_available", lambda: True)
    monkeypatch.setattr(tts, "_omni", lambda settings=None: FakeOmni())
    ref = tmp_path / "vocals.wav"
    _fake_audio(ref, 12, 200)
    segs = [Segment(0.5, 3.0, "你好", "Xin chào", "S1"), Segment(3.5, 6.0, "走吧", "Đi thôi", "S2")]
    st = {}
    tts.synthesize_dub(segs, tmp_path / "dub.wav", 8, tmp_path / "t",
                       voices={"S1": f"omni:{v['id']}", "S2": tts.OMNI_CLONE}, ref_audio=ref, stats=st)
    assert st["placed"] == 2
    a = [c for c in calls if c[0] == "Xin chào"][0]
    assert a[1] == voices.path_of(v).name and a[2] == "Xin chào các bạn"   # giọng mẫu + lời mẫu
    b = [c for c in calls if c[0] == "Đi thôi"][0]
    assert b[1] == "omniref_S2.wav" and b[2] == "走吧"                     # mẫu cắt từ video + lời gốc
    assert any(x["id"] == f"omni:{v['id']}" for x in tts.list_voices({}))
    voices.delete(v["id"])
    assert voices.list_samples() == []


def test_dub_placement_no_overlap(tmp_path, monkeypatch):
    """Xếp giọng lồng: không câu nào chồng câu nào; câu quá dài bị đánh dấu 'long' và nằm trong too_long."""
    from trami import tts

    durs = {"vừa": 0.8, "dài": 1.6, "quá dài": 3.0, "cuối": 0.5}

    async def fake(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, t, p, v in items:
            _fake_audio(p.with_suffix(".x.wav"), durs[t], 300)
            p.with_suffix(".x.wav").replace(p)
        return []
    monkeypatch.setattr(tts, "_edge_all", fake)
    segs = [Segment(1.0, 2.0, "a", "vừa"), Segment(2.4, 3.4, "b", "dài"), Segment(3.6, 4.4, "c", "quá dài"),
            Segment(4.7, 5.4, "d", "cuối")]
    st = {}
    tts.synthesize_dub(segs, tmp_path / "o.wav", 7, tmp_path / "w", max_speedup=1.3, stats=st)
    lay = st["layout"]
    assert all(lay[i][1] <= lay[i + 1][0] + 1e-6 for i in range(len(lay) - 1))
    fits = {k: f for _, _, k, f in lay}
    assert fits[0] == "ok" and fits[2] == "long" and [k for k, _, _ in st["too_long"]] == [2]
    assert segs[2].extra["fit"] == "long"


def test_align_split_at_pause_and_speaker_change():
    from trami import align

    sr = 16000
    a = np.zeros(sr * 6, np.float32)
    n1, n2 = np.arange(int(1.2 * sr)), np.arange(int(1.0 * sr))
    a[int(1.2 * sr):int(1.2 * sr) + len(n1)] = 0.3 * np.sin(2 * np.pi * 220 * n1 / sr)
    a[int(3.0 * sr):int(3.0 * sr) + len(n2)] = 0.3 * np.sin(2 * np.pi * 220 * n2 / sr)
    out = align.refine([Segment(0.8, 4.5, "你好朋友，我们走吧。", "")], a, use_fa=False)
    assert [x.text for x in out] == ["你好朋友，", "我们走吧。"]
    assert abs(out[0].start - 1.15) < 0.1 and abs(out[1].end - 4.05) < 0.1
    b = np.zeros(sr * 6, np.float32)
    n = np.arange(sr * 3)
    b[:sr * 3] = 0.3 * np.sin(2 * np.pi * 200 * n / sr)
    b[sr * 3:] = 0.3 * np.sin(2 * np.pi * 900 * n / sr)

    def emb(w):
        sp = np.abs(np.fft.rfft(w))[:2000]
        return sp / (np.linalg.norm(sp) + 1e-9)
    # không dấu câu, không mốc từng chữ -> KHÔNG cắt (trước đây cắt theo ước lượng, hay cắt đôi 1 từ)
    parts = align.split_speaker_changes([Segment(0, 6, "我们一起去吃饭吧好不好你说呢", "")], b, emb)
    assert len(parts) == 1
    # có dấu phẩy gần chỗ đổi giọng -> cắt đúng ở dấu phẩy
    parts = align.split_speaker_changes([Segment(0, 6, "我们一起去吃饭吧，好不好你说呢", "")], b, emb)
    assert [p.text for p in parts] == ["我们一起去吃饭吧，", "好不好你说呢"] and abs(parts[0].end - 3.0) < 0.6
    # có mốc từng chữ thật (fa-zh) và chỗ nghỉ ở 3,0 s -> cắt ở chỗ nghỉ dù không có dấu câu
    ct = [[i * 0.4, i * 0.4 + 0.35] for i in range(7)] + [[3.1 + i * 0.4, 3.45 + i * 0.4] for i in range(7)]
    seg = Segment(0, 6, "我们一起去吃饭吧好不好你说呢", "")
    seg.extra["ct"] = ct
    parts = align.split_speaker_changes([seg], b, emb)
    assert [p.text for p in parts] == ["我们一起去吃饭", "吧好不好你说呢"]


def test_plan_cuts_sentences():
    """Tách câu trọn nghĩa: cắt ở cuối câu (。！？) / chỗ nghỉ thật; dấu phẩy nói liền KHÔNG cắt (lỗi "câu cụt"),
    phẩy + nghỉ ≥ 0,45 s thì cắt; câu quá dài chia ở dấu phẩy gần giữa; không cắt đôi từ / số."""
    from trami import align

    def run(text, exact=False, gaps=None, max_chars=22, per=0.2):
        pos = align._char_positions(text)
        t, times = 0.0, []
        for k in range(len(pos)):
            times.append((t, t + per))
            t += per + ((gaps or {}).get(k, 0.0))
        cuts = align.plan_cuts(text, pos, times, max_chars, exact)
        return align._split_text(text, pos, cuts) if cuts else [text]

    # phẩy nói liền: 1 câu trọn vẹn (trước đây bị cắt vụn thành 3 dòng)
    assert run("爸最好了，6块钱可以干啥，你根本不懂。") == ["爸最好了，6块钱可以干啥，你根本不懂。"]
    # phẩy + nghỉ 0,6 s (hết câu / đổi lượt nói) -> tách; số đi theo phần sau ("6" | "块钱" là lỗi cũ)
    assert run("爸最好了，6块钱可以干啥，你根本不懂。", gaps={3: 0.6}) == ["爸最好了，", "6块钱可以干啥，你根本不懂。"]
    # cuối câu luôn tách
    assert run("你去哪里了？我在家等你。") == ["你去哪里了？", "我在家等你。"]
    # câu quá dài: chia ở dấu phẩy gần giữa, không ở chỗ bất kỳ
    assert run("我们今天去公园玩，然后去吃饭，最后回家睡觉。", max_chars=12) == ["我们今天去公园玩，", "然后去吃饭，最后回家睡觉。"]
    # nói chậm quá 8 giây: chia ở dấu phẩy dù chưa quá số chữ
    assert run("我们今天去公园玩，然后去吃饭了。", max_chars=28, per=0.6) == ["我们今天去公园玩，", "然后去吃饭了。"]
    # mẩu ngắn "哇，" gộp vào câu sau; câu dài không dấu câu cắt ở ranh giới từ
    out = run("哇，这个形态的GPT好可爱啊")
    assert out == ["哇，这个形态的GPT好可爱啊"]
    out = run("这臭中转站到底黑了我们多少优惠啊没给用户留下豆包就不错了", max_chars=16)
    assert len(out) == 2 and all(len(x) <= 16 for x in out)
    assert not any(x.startswith("们") or x.endswith("我") for x in out)    # không cắt đôi "我们"
    # có mốc thật: nghỉ dài 0,8 s giữa 2 vế không dấu câu -> tách; nghỉ ngắn -> không
    assert run("快想办法给我变回去估计额度用完了", exact=True, gaps={8: 0.8}) == ["快想办法给我变回去", "估计额度用完了"]
    assert run("快想办法给我变回去估计额度用完了", exact=True, gaps={8: 0.2}) == ["快想办法给我变回去估计额度用完了"]
    # chỗ nghỉ có chữ Latin xen giữa (GPT đang được nói) -> không cắt
    assert run("这个形态的GPT好可爱啊", exact=True, gaps={4: 0.9}) == ["这个形态的GPT好可爱啊"]


def test_chunks_and_word_times():
    """Gộp đoạn có tiếng sát nhau thành khúc ≤ 25 s (nghe trọn câu), cắt khúc dài ở chỗ nghỉ dài nhất; mốc từng chữ từ
    kết quả có mốc thời gian (từ nhiều chữ chia đều, dấu câu / số bỏ qua)."""
    from trami import align

    a = np.zeros(16000 * 60, np.float32)
    assert align.chunk_spans([(0, 3), (3.5, 6), (8, 10)], a) == [(0, 6), (8, 10)]
    out = align.chunk_spans([(0, 9), (9.5, 18), (19.5, 27), (27.3, 33)], a, max_len=25)
    assert out == [(0, 18), (19.5, 33)]          # cắt ở chỗ nghỉ 1,5 s, không ở chỗ nghỉ 0,3 s
    assert all(b - x <= 25 for x, b in align.chunk_spans([(0, 40)], a))   # VAD cắt cứng -> cắt ở chỗ lặng nhất
    ct = align.times_from_words("快看，我爸给6块！", [("快", 0, .2), ("看", .2, .4), ("，", .4, .5), ("我爸", .6, 1.0),
                                                   ("给", 1, 1.2), ("6", 1.2, 1.4), ("块", 1.4, 1.6)], 10)
    assert ct == [(10.0, 10.2), (10.2, 10.4), (10.6, 10.8), (10.8, 11.0), (11.0, 11.2), (11.4, 11.6)]
    assert align.times_from_words("快看我", [("快", 0, .2)]) is None   # lời đã đổi -> không dùng mốc sai
    # đoạn có mốc đo sẵn (extra["ct"]): refine tách câu theo mốc đó, không đoán lại
    seg = Segment(10, 14, "好了，不要闹了。待会儿变回来")
    pos = align._char_positions(seg.text)
    seg.extra["ct"] = [[10 + 0.25 * k, 10 + 0.25 * k + 0.2] for k in range(len(pos))]
    out = align.refine([seg], np.zeros(16000 * 20, np.float32), use_fa=False)
    assert [s.text for s in out] == ["好了，不要闹了。", "待会儿变回来"]
    assert abs(out[1].start - (10 + 0.25 * 6)) < 0.06 and "ct" in out[1].extra


def test_rescue_missed_speech():
    """Đoạn có tiếng mà bộ chia đoạn bỏ sót -> nghe lại; bỏ câu trùng câu sát bên và mẩu 1 chữ."""
    from trami import align

    sr = 16000
    a = np.zeros(sr * 12, np.float32)
    t = np.arange(sr * 2) / sr
    for s0, d in ((1, 1.6), (4, 1), (7, 1), (10, 1)):   # các đoạn có tiếng
        a[int(s0 * sr):int((s0 + d) * sr)] = 0.3 * np.sin(2 * np.pi * 220 * t[:int(d * sr)])
    segs = [Segment(0.95, 2.05, "第一句话")]   # chỉ nhận dạng được đoạn đầu (mốc hơi hụt cuối)
    queue = iter(["你别过来", "第一句话", "啊"])

    def decode(clip):
        d = len(clip) / sr
        if d > 1.6:
            return "第一句话", ["Speech"]           # nghe gộp với câu sát bên: không dài hơn -> giữ câu cũ
        if d < 0.9:
            return "一句话", ["Speech"]             # đuôi câu sát bên
        return next(queue), ["Speech"]

    wins = align.missed_speech([(s.start, s.end) for s in segs], a)
    assert [round(w[0]) for w in wins] == [2, 4, 7, 10]
    assert all(w[2] >= 2.05 for w in wins)              # cửa sổ nghe lại không lấn vào câu đã có
    new = align.rescue_missed(segs, a, decode)
    # "一句话" = đuôi câu sát bên -> bỏ; "第一句话" ở xa 5 giây là câu nói lại -> giữ; "啊" 1 chữ -> bỏ
    assert [n.text for n in new] == ["你别过来", "第一句话"]
    assert all(n.extra.get("rescued") for n in new) and 3.9 < new[0].start < 4.1
    assert not segs[0].extra.get("absorbed")

    # nửa câu bị VAD bỏ ("不要再") nằm sát nửa kia đã nhận dạng ("说") -> nghe gộp, thay bằng 1 câu trọn vẹn
    b = np.zeros(sr * 4, np.float32)
    b[int(1.0 * sr):int(2.0 * sr)] = 0.3 * np.sin(2 * np.pi * 220 * t[:sr])
    half = [Segment(1.6, 2.0, "说。")]
    new = align.rescue_missed(half, b, lambda clip: ("不要再说了。" if len(clip) > 0.9 * sr else "不要再。", ["Speech"]))
    assert [n.text for n in new] == ["不要再说了。"] and half[0].extra.get("absorbed")
    assert new[0].start <= 1.05 and new[0].end >= 1.95


@pytest.mark.parametrize("model", ["vocals_mel_band_roformer.ckpt",
                                   "ens:model_bs_roformer_ep_317_sdr_12.9755.ckpt+vocals_mel_band_roformer.ckpt"])
def test_separate_picks_right_stems(tmp_path, monkeypatch, model):
    """Mel-RoFormer ghi phần nhạc nền TRƯỚC, tên mặc định lại chứa chữ "vocals" (tên mô hình) -> không được lấy nhầm."""
    import types

    from trami import separate as sp

    src = tmp_path / "a.wav"
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    str(src)], check=True)
    seen = {}

    class FakeSep:
        def __init__(self, output_dir, model_file_dir, output_format, log_level, **kw):
            self.out = Path(output_dir)
            seen["kw"] = kw

        def load_model(self, model_filename):
            seen["model"] = model_filename

        def separate(self, path, names):
            other = self.out / "mix44k_(Other)_vocals_mel_band_roformer.wav"   # bản cũ bỏ qua tên tuỳ chỉnh
            voc = self.out / "vocals.wav"
            subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-i", path, "-af", "volume=0.1", str(other)], check=True)
            shutil.copy(path, voc)
            return [other.name, voc.name]

    mod = types.ModuleType("audio_separator.separator")
    mod.Separator = FakeSep
    monkeypatch.setitem(sys.modules, "audio_separator", types.ModuleType("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", mod)
    monkeypatch.setattr(sp, "available", lambda: True)
    v, i = sp.separate(src, tmp_path / "stems", model)
    loud = lambda f: float(np.abs(np.frombuffer(open(f, "rb").read()[44:], np.int16)).mean())  # noqa: E731
    assert loud(v) > 5 * loud(i)          # file "vocals" là giọng thật, không phải phần nhạc nền
    if model.startswith("ens:"):
        assert seen["model"] == sp.model_list(model) and seen["kw"] == {"ensemble_algorithm": "avg_wave"}
    else:
        assert seen["model"] == model and seen["kw"] == {}


def test_ocr_drops_static_watermark():
    """Chữ đứng yên gần suốt video (watermark "冬冰 bilibili") không được dính vào phụ đề."""
    from trami.ocr import _is_mark, static_marks, subtitle_height

    def box(x, y, w=60, h=30):
        return np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], np.float32)

    frames = []
    for k in range(400):                     # 100 giây, 4 khung/giây
        wm = [(box(972, 83, 56), "冬冰", 0.99), (box(1003, 86, 24), "水", 0.98), (box(976, 88, 25), "冬", 0.96),
              (box(972, 80, 280, 36), "冬冰b#", 0.9)][k % 4 if k % 7 == 0 else 0]   # OCR đọc lệch / dính logo
        items = [wm]
        if k % 40 < 12:                      # câu thoại: chữ cao 65 px giữa màn hình
            items.insert(0, (box(380, 10, 270, 65), f"你根本不懂{k // 40}", 0.99))
        if 300 <= k < 304:
            items.insert(0, (box(397, 67, 34, 41), "米", 0.97))   # hoa văn trên áo
        frames.append((k / 4, items))
    marks = static_marks(frames, 4.0)
    assert [m[0] for m in marks] == ["冬冰"]
    h = subtitle_height(frames, marks)
    assert 60 < h < 70
    kept = {"".join(it[1] for it in items if not _is_mark(it, marks) and _bbox_h(it) >= 0.7 * h)
            for _, items in frames}
    assert kept == {""} | {f"你根本不懂{i}" for i in range(10)}
    assert not static_marks(frames[:30], 4.0)   # video quá ngắn / câu thoại ngắn -> không coi là watermark


def _bbox_h(item):
    return float(item[0][:, 1].max() - item[0][:, 1].min())


def test_ocr_change_detector_any_color():
    """Bước "chữ có đổi không" nhận ra chữ MỌI màu (trước chỉ nhận trắng / vàng có viền -> chữ hồng không bao giờ được
    đọc) và câu mới hiện trên cảnh sáng (trước chỉ đổi 14 % -> bị coi là câu cũ); nhiễu nén nhẹ không tính là đổi."""
    import cv2
    from trami import ocr

    bg = np.full((180, 1280, 3), (200, 200, 200), np.uint8)               # cảnh sáng
    pink = bg.copy()
    for k in range(8):                                                     # 8 "chữ" nét hồng
        x = 380 + k * 66
        cv2.rectangle(pink, (x, 60), (x + 44, 110), (180, 110, 255), 4)
        cv2.line(pink, (x, 85), (x + 44, 85), (180, 110, 255), 4)
    noisy = np.clip(bg.astype(np.int16) + np.random.default_rng(0).integers(-3, 4, bg.shape), 0, 255).astype(np.uint8)
    a, b, c = ocr._text_signature(bg), ocr._text_signature(pink), ocr._text_signature(noisy)
    assert ocr._changed(a, b) and not ocr._changed(a, c)
    # cùng câu đọc lệch 1 chữ = 1 câu; 2 câu na ná nhau (khác 2 chữ) = 2 câu
    assert ocr._same_line("没想到你也有这一天", "没想到你也有这—天")
    assert not ocr._same_line("这是黄色的字幕测试", "这是粉红色的字幕测试")


def _cjk_font():
    for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
              "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(f):
            return f
    return None


def test_ocr_region_multiline_and_colors(tmp_path):
    """Dò vùng phụ đề từ câu đầu tiên (nới lên / xuống, hết chiều ngang) + đọc đủ: chữ hồng, phụ đề 2–3 dòng (trước chỉ
    đọc được dòng dưới cùng), câu dài gần hết ngang; watermark góc không lẫn vào câu."""
    pytest.importorskip("rapidocr_onnxruntime")
    font = _cjk_font()
    if not font:
        pytest.skip("không có font chữ Hán để vẽ video thử")
    from trami import ocr

    font_ff = font.replace("\\", "/").replace(":", "\\:")
    subs = [(0.8, 2.4, ["第一句只有一行字幕"], "white"), (2.4, 4.0, ["这是粉红色的字幕测试"], "0xFF6EB4"),
            (4.0, 5.6, ["这是第二句的第一行", "第二句的第二行在下面"], "white"),
            (5.6, 7.2, ["三行字幕的第一行", "三行字幕的第二行", "三行字幕的第三行"], "white"),
            (7.2, 8.8, ["这是一句非常非常长的字幕从左边一直写到右边几乎占满整个画面"], "white")]
    f = []
    for a, b, lines, col in subs:
        for k, line in enumerate(reversed(lines)):
            f.append(f"drawtext=fontfile='{font_ff}':text='{line}':fontsize={30 if len(line) > 20 else 34}:"
                     f"fontcolor={col}:borderw=2:bordercolor=black:x=(w-tw)/2:y=h-70-{k * 46}:"
                     f"enable='between(t,{a},{b - 0.04})'")
    f.append(f"drawtext=fontfile='{font_ff}':text='频道水印':fontsize=20:fontcolor=white:x=w-110:y=h-36")
    v = tmp_path / "subs.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x203040:s=960x540:r=25:d=9.2",
                    "-vf", ",".join(f), "-pix_fmt", "yuv420p", str(v)], check=True)
    r = ocr.detect_subtitle_region(v)
    assert r["found"] and r["x"] == 0 and r["w"] == 1 and abs(r["first_t"] - 0.8) < 0.3
    assert r["y"] < 0.75 and r["y"] + r["h"] > 0.9            # có chỗ cho 3 dòng phía trên dòng đầu
    texts = [s.text for s in ocr.extract_hardsubs(v, r)]
    for _, _, lines, _ in subs:
        assert any(ocr._similar("".join(lines), t) >= 0.85 for t in texts), ("".join(lines), texts)
    assert not any("水印" in t or "频道" in t for t in texts)


def test_ass_custom_position_and_box_sizes():
    """Kéo phụ đề trên video -> \\pos đúng chỗ; nền hộp ôm chữ dùng \\xbord/\\ybord; dải nền cố định vẽ riêng."""
    segs = [Segment(0, 2, "你好", "Xin chào các bạn")]
    a = st.to_ass(segs, {"position": "custom", "pos_x": 0.3, "pos_y": 0.5, "size": 100}, 1920, 1080)
    assert "{\\an2\\pos(576,540)}Xin chào các bạn" in a
    a = st.to_ass(segs, {"position": "bottom", "box": True, "box_pad_x": 0.5, "box_pad_y": 0.1, "size": 100},
                  1920, 1080)
    assert "\\xbord50.0\\ybord10.0" in a and ",3,10.0," in a          # BorderStyle 3, đệm dọc 10 px
    a = st.to_ass(segs, {"position": "custom", "pos_x": 0.3, "pos_y": 0.5, "size": 100, "box": True,
                         "box_fixed": True, "box_w": 0.4, "box_h": 0.2, "box_color": "#FF0000",
                         "box_opacity": 1.0}, 1920, 1080)
    draw = [ln for ln in a.splitlines() if "\\p1" in ln]
    # khối chữ 1 dòng cao 100, chân ở y=540 -> tâm 490; dải 768×216 -> góc trên trái (192, 382)
    assert len(draw) == 1 and draw[0].startswith("Dialogue: 0,") and "\\pos(192,382)" in draw[0]
    assert "m 0 0 l 768 0 768 216 0 216" in draw[0] and "\\1c&H0000FF&" in draw[0] and "\\1a&H00&" in draw[0]
    assert any(ln.startswith("Dialogue: 1,") and ln.endswith("Xin chào các bạn") for ln in a.splitlines())
    # song ngữ: dòng Trung reset kiểu "Source" vẫn giữ đệm nền
    a = st.to_ass(segs, {"box": True, "box_pad_x": 0.3, "size": 60}, 1280, 720, "both")
    assert "\\N{\\rSource\\xbord18.0" in a


def test_region_drag_handles():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPointF, QRectF

    from trami.gui.editors import drag_region, hit_region

    r = {"x": 0.2, "y": 0.8, "w": 0.6, "h": 0.1}
    rr = QRectF(200, 800, 600, 100)
    assert hit_region(rr, QPointF(500, 800)) == "T" and hit_region(rr, QPointF(800, 850)) == "R"
    assert hit_region(rr, QPointF(200, 900)) == "BL" and hit_region(rr, QPointF(400, 850)) == "move"
    assert hit_region(rr, QPointF(100, 100)) == ""
    t = drag_region(r, "T", 0, -0.1)                      # kéo cạnh trên lên: cao hơn, đáy giữ nguyên
    assert abs(t["y"] - 0.7) < 1e-9 and abs(t["y"] + t["h"] - 0.9) < 1e-9
    w = drag_region(r, "R", 0.5, 0)                       # kéo quá mép phải -> dừng ở mép
    assert abs(w["x"] + w["w"] - 1.0) < 1e-9
    m = drag_region(r, "move", -0.5, 0.5)                 # di chuyển: giữ cỡ, không ra ngoài khung
    assert m["x"] == 0.0 and abs(m["y"] - 0.9) < 1e-9 and m["w"] == r["w"] and m["h"] == r["h"]
    s = drag_region(r, "L", 0.9, 0)                       # kéo cạnh trái vượt cạnh phải -> còn bề rộng tối thiểu
    assert s["w"] >= 0.01 - 1e-9


def test_inpaint_cached_reused(tmp_path, monkeypatch):
    """'Chạy xoá bằng AI' rồi Xuất video: cùng vùng -> dùng lại, đổi vùng -> chạy lại và xoá bản cũ."""
    from trami import inpaint, video

    src = tmp_path / "v.mp4"
    src.write_bytes(b"x" * 100)
    calls = []

    def fake(src_, out, regions, settings, boxes_file=None, progress=None, cancel=None, strength="normal", **kw):
        calls.append(regions)
        Path(out).write_bytes(b"done")
    monkeypatch.setattr(inpaint, "inpaint_video", fake)
    hs = {"mode": "inpaint", "regions": [{"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1}], "strength": "normal"}
    sts = {"inpaint_backend": "opencv", "video_crf": 20}
    a = video.inpaint_cached(src, hs, sts, tmp_path / "w")
    b = video.inpaint_cached(src, hs, sts, tmp_path / "w")
    assert a == b and len(calls) == 1 and a.read_bytes() == b"done"
    hs2 = {**hs, "regions": [{"x": 0.1, "y": 0.75, "w": 0.8, "h": 0.15}]}
    c = video.inpaint_cached(src, hs2, sts, tmp_path / "w")
    assert c != a and len(calls) == 2 and not a.exists()
    assert video.inpainted_file(src, hs2, sts, tmp_path / "w") == c
    # chỉ gỡ logo (cách xoá phụ đề khác): vẫn cần bản trung gian; vùng phụ đề không ảnh hưởng mã khi không xoá AI
    hs3 = {"mode": "blur", "regions": hs["regions"], "logos": [{"x": 0.8, "y": 0.02, "w": 0.15, "h": 0.08}]}
    assert video.needs_clean(hs3) and not video.needs_clean({"mode": "blur", "regions": hs["regions"]})
    assert video.inpaint_key(src, hs3, sts) == video.inpaint_key(src, {**hs3, "regions": []}, sts)
    video.inpaint_cached(src, hs3, sts, tmp_path / "w")
    assert calls[-1] == []          # chỉ gỡ logo, không xoá vùng phụ đề bằng AI


def test_vi_numbers_spoken():
    """Bộ đọc AI: số đọc thành chữ, giờ / ngày / % / $; số không chắc cách đọc (phiên bản, tỉ số) giữ nguyên."""
    from trami.voicefix import read_int, spoken_vi

    assert read_int("21") == "hai mươi mốt" and read_int("15") == "mười lăm" and read_int("105") == "một trăm linh năm"
    assert read_int("1005") == "một nghìn không trăm linh năm" and read_int("24") == "hai mươi tư"
    assert read_int("2500000000000") == "hai nghìn năm trăm tỷ" and read_int("007") == "không không bảy"
    assert spoken_vi("Giá 90.000 đồng, giảm 50%") == "Giá chín mươi nghìn đồng, giảm năm mươi phần trăm"
    assert spoken_vi("Năm 2026.") == "Năm hai nghìn không trăm hai mươi sáu."
    assert spoken_vi("Có 3,5 triệu") == "Có ba phẩy năm triệu"
    assert spoken_vi("Lúc 10:30 ngày 12/10/2026").startswith("Lúc mười giờ ba mươi ngày mười hai tháng mười năm")
    assert spoken_vi("Đứng thứ 4") == "Đứng thứ tư"
    for keep in ("Phiên bản 1.2.3", "Tỉ số 3/4 hiệp", "iPhone15", "3.14"):
        assert spoken_vi(keep) == keep


def test_voice_problem_and_levels():
    """Phát hiện giọng AI hỏng (im lặng / bíp / còi / ù) mà không báo nhầm tiếng nói; cân âm lượng chỉ tăng câu nhỏ."""
    from trami.voicefix import level_gains, speech_level, voice_problem

    sr = 24000
    t = np.arange(int(1.5 * sr)) / sr
    speech = _speech_like(sr, 1.5)
    assert voice_problem(speech, sr) is None
    assert voice_problem(np.zeros(sr, np.float32), sr) == "toàn khoảng lặng"
    assert voice_problem((0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32), sr)
    assert voice_problem(np.full(sr, 0.2, np.float32), sr)
    beep = 0.3 * np.sin(2 * np.pi * 1500 * t[: int(0.15 * sr)])
    gap = np.zeros(int(0.1 * sr))
    assert voice_problem(np.concatenate([beep, gap, beep, gap, speech]).astype(np.float32), sr)
    whistle = 0.25 * np.sin(2 * np.pi * 2000 * t[: int(0.4 * sr)])
    assert voice_problem(np.concatenate([speech[:sr], whistle, speech[sr:]]).astype(np.float32), sr)
    lv = speech_level(speech, sr)
    assert abs(speech_level(speech * 0.5, sr) - lv / 2) < lv * 0.05
    g = level_gains({0: lv, 1: lv, 2: lv, 3: lv * 0.25}, {k: "A" for k in range(4)})
    assert set(g) == {3} and abs(g[3] - 10 ** (9 / 20)) < 1e-6        # tăng tối đa +9 dB, câu thường không đổi


def test_gender_pitch_and_auto_assign():
    from trami import tts
    from trami.gender import SR, pitch_track, speaker_gender

    t = np.arange(SR * 2) / SR
    for f in (110, 220, 300):
        x = (0.3 * np.sin(2 * np.pi * f * t) + 0.15 * np.sin(4 * np.pi * f * t)).astype(np.float32)
        assert abs(np.median(pitch_track(x)) - f) < f * 0.03
    low = (0.3 * np.sin(2 * np.pi * 115 * t) + 0.1 * np.sin(4 * np.pi * 115 * t)).astype(np.float32)
    high = (0.3 * np.sin(2 * np.pi * 240 * t) + 0.1 * np.sin(4 * np.pi * 240 * t)).astype(np.float32)
    assert speaker_gender(low, [(0, 2)])["g"] == "male" and speaker_gender(high, [(0, 2)])["g"] == "female"
    g = {"S1": {"g": "male"}, "S2": {"g": "female"}, "S3": {"g": "female"}}
    v = tts.auto_assign(["S1", "S2", "S3"], {"tts_engine": "edge"}, {}, g)
    assert v == {"S1": "edge:vi-VN-NamMinhNeural", "S2": "edge:vi-VN-HoaiMyNeural", "S3": "edge:vi-VN-HoaiMyNeural"}


def test_hymt_prompt_and_patch_source():
    from trami.hymt_worker import build_prompt, clean_output
    from trami.video import patch_source

    p = build_prompt("你好", ["前一句"], [["李逍遥", "Lý Tiêu Dao"]])
    assert p.startswith("参考下面的翻译：\n李逍遥 翻译成 Lý Tiêu Dao") and p.endswith("也不要额外解释：\n你好")
    assert build_prompt("你好") == "把下面的文本翻译成越南语，不要额外解释。\n\n你好"
    assert clean_output("<think>x</think> Xin  chào ") == "Xin chào"
    r = {"x": 0.2, "y": 0.8, "w": 0.6, "h": 0.1}
    assert abs(patch_source(r) - (0.8 - 0.1 - 0.01)) < 1e-9                 # mặc định lấy dải phía trên
    assert abs(patch_source(r, "below") - 0.7) < 1e-9 or patch_source(r, "below") <= 0.9  # dưới không đủ chỗ
    top = {"x": 0, "y": 0.02, "w": 1, "h": 0.1}
    assert patch_source(top) > top["y"]                                      # vùng sát mép trên -> lấy phía dưới


def test_audio_settings_and_live_mix():
    """Tab Âm thanh: dự án cũ suy ra đúng cách trộn cũ; bộ trộn trực tiếp: âm lượng, tắt, nghe riêng, đoạn hát,
    nhạc thêm theo thời gian sau cắt + giảm khi có lời, mờ dần, phát nhanh."""
    from trami.audiomix import SR, LiveMix, audio_settings, gains, use_stems

    old = {"volume": 0.5, "mute_original": False, "pitch_shift": 0,
           "dub": {"enabled": True, "original_volume": 0.15, "bg_volume": 1.2, "vocals_volume": 0.0, "dub_volume": 1.5},
           "bgm": {"path": "", "volume": 0.3, "duck": False}}
    a = audio_settings(old)
    assert abs(a["orig"] - 0.075) < 1e-9 and abs(a["bg"] - 0.6) < 1e-9 and a["dub"] == 1.5 and a["bgm"] == 0.3
    assert a["duck"] is False and a["pitch"] == 1.0 and a["mute"] == []
    assert audio_settings({**old, "mute_original": True})["mute"] == ["orig", "bg", "voice"]
    assert not use_stems(a, False, True) and use_stems(a, True, True) and not use_stems(a, True, False)
    assert use_stems({**a, "src": "stems"}, True, False) and not use_stems({**a, "src": "orig"}, True, True)
    g = gains({**a, "master": 2.0, "mute": ["dub"]}, True, True, False)
    assert g["orig"] == 0 and abs(g["bg"] - 1.2) < 1e-9 and g["dub"] == 0 and g["voice_song"] == 2.0

    n = SR * 4
    one = np.full((n, 2), 10000, np.int16)          # mỗi lớp = hằng số 10000/32768 ≈ 0,305
    mix = LiveMix()
    mix.set_tracks({"bg": one, "voice": one, "dub": np.zeros((n, 2), np.int16), "bgm": one[: SR]})
    mix.g = {"bg": 0.5, "voice": 0.0, "voice_song": 1.0, "dub": 1.0, "bgm": 0.0}
    mix.songs = [(1.0, 2.0)]
    out, adv = mix.render(0, SR * 3)
    lvl = 10000 / 32768
    assert adv == SR * 3 and abs(out[SR // 2, 0] - 0.5 * lvl) < 1e-3          # chỉ nhạc nền 50 %
    assert abs(out[int(SR * 1.5), 0] - 1.5 * lvl) < 1e-3                        # đoạn hát: + giọng gốc 100 %
    mix.g["bg"] = 1.0                                                           # đổi âm lượng: khối sau có ngay
    assert abs(mix.render(0, 100)[0][0, 0] - lvl) < 1e-3
    mix.solo = "voice"
    assert abs(mix.render(int(SR * 1.5), 10)[0][0, 0] - lvl) < 1e-3 and abs(mix.render(0, 10)[0][0, 0]) < 1e-6
    mix.solo = None
    # nhạc thêm: theo thời gian SAU CẮT (đoạn 0–1 s bị cắt) và lặp lại; có lời lồng tiếng -> giảm 16 dB
    mix.g = {"bgm": 1.0, "dub": 1.0}
    mix.keep = [(1.0, 4.0)]
    bgm = np.zeros((SR, 2), np.int16)
    bgm[: SR // 2] = 10000
    dub = np.zeros((n, 2), np.int16)
    dub[int(SR * 2.5):int(SR * 3)] = 20000
    mix.set_tracks({"bgm": bgm, "dub": dub})
    mix.duck = True
    assert abs(mix.render(int(SR * 1.2), 1)[0][0, 0] - lvl) < 1e-3     # 0,2 s sau chỗ cắt = nửa đầu file nhạc
    assert abs(mix.render(int(SR * 1.7), 1)[0][0, 0]) < 1e-6           # 0,7 s = nửa sau (lặng)
    on = mix.render(int(SR * 2.2), 1)[0][0, 0]                         # 1,2 s sau cắt -> vòng lặp 2, có nhạc
    ducked = mix.render(int(SR * 3.1), 1)[0][0, 0]       # lời lồng tiếng vừa hết 0,1 s: nhạc vẫn đang nhỏ (nhả 0,4 s)
    back = mix.render(int(SR * 3.45), 1)[0][0, 0]        # hết lời > 0,4 s: nhạc to lại
    assert abs(on - lvl) < 1e-3 and abs(ducked - lvl * 10 ** (-16 / 20)) < 2e-3 and abs(back - lvl) < 1e-3
    # mờ dần đầu 1 s (tính sau cắt) + phát nhanh 2×: 1 khối đi qua gấp đôi thời gian video
    mix.set_tracks({"bg": one})
    mix.g, mix.keep, mix.fade, mix.duck = {"bg": 1.0}, [], (1.0, 0.0), False
    assert abs(mix.render(SR // 2, 1)[0][0, 0] - 0.5 * lvl) < 2e-3
    mix.fade, mix.rate = (0.0, 0.0), 2.0
    out, adv = mix.render(0, 1000)
    assert adv == 2000 and out.shape == (1000, 2)


def test_karaoke_timing_and_ass():
    from trami import karaoke as K

    assert K.tokens("Xin chào,\ncác bạn!") == [("Xin", " "), ("chào,", "\n"), ("các", " "), ("bạn!", "")]
    assert [t for t, _ in K.tokens("你好，世界。")] == ["你", "好，", "世", "界。"]
    text = "Xin chào các bạn, hôm nay trời đẹp quá!"
    # không có giọng lồng: rải trong thời gian câu, nghỉ sau dấu phẩy
    tt = K.word_times(text, 1.0, 4.0)
    assert len(tt) == 9 and tt[0][0] == 1.0 and abs(tt[-1][1] - 4.0) < 1e-6
    assert all(a <= b for a, b in tt) and all(tt[i][1] <= tt[i + 1][0] + 1e-9 for i in range(8))
    assert tt[4][0] - tt[3][1] > 0.05                       # chờ ở dấu phẩy
    # giọng lồng ngắt 2 lần đúng 2 vế -> mỗi vế nằm trong đoạn giọng của nó
    tt = K.word_times(text, 1.0, 4.0, [[1.2, 2.0], [2.4, 3.6]])
    assert tt[0][0] == 1.2 and abs(tt[3][1] - 2.0) < 1e-6 and tt[4][0] == 2.4 and abs(tt[-1][1] - 3.6) < 1e-6
    # giọng dài quá thời gian câu -> co lại cho chạy hết trước khi câu tắt
    tt = K.word_times("một hai ba", 1.0, 2.0, [[1.0, 3.0]])
    assert abs(tt[-1][1] - 2.0) < 1e-6
    assert K.progress([(1.0, 2.0)], 1.5) == [0.5] and K.progress([(1.0, 2.0)], 1.5, "word") == [1.0]
    a = K.ass_text(text, 1.0, 4.0, [[1.2, 2.0], [2.4, 3.6]])
    assert a.startswith("{\\k20}{\\kf20}Xin ") and a.count("\\kf") == 9 and "{\\k40}{\\kf" in a
    assert sum(int(x) for x in re.findall(r"\\kf?(\d+)", a)) <= 300
    assert K.ass_text("a b", 0, 1, mode="word").count("{\\k") == 2
    # đoạn có tiếng đo từ sóng âm rút gọn
    env = np.zeros(80)
    env[24:40] = 0.8
    env[48:72] = 0.6
    assert K.voiced(env, 0.05, 1.0, 3.8) == [[1.2, 2.0], [2.4, 3.6]]
    # ASS: chữ chưa đọc = màu chữ (Secondary), đã đọc = màu karaoke (Primary)
    segs = [Segment(1.0, 4.0, "你好", text, extra={"kv": [[1.2, 2.0], [2.4, 3.6]]})]
    ass = st.to_ass(segs, {"karaoke": True, "karaoke_color": "#FFEF00", "color": "#FFFFFF", "font": "Bangers"},
                    1280, 720)
    sty = next(ln for ln in ass.splitlines() if ln.startswith("Style: Default")).split(",")
    assert sty[1] == "Bangers" and sty[3] == "&H0000EFFF" and sty[4] == "&H00FFFFFF"
    assert "{\\k20}{\\kf20}Xin" in ass
    # nền ôm chữ + karaoke: nền ở lớp 0 (chữ ẩn), chữ karaoke lớp 1 không nền -> không sọc giữa các từ
    ass = st.to_ass(segs, {"karaoke": True, "box": True}, 1280, 720, which="both")
    ev = [ln for ln in ass.splitlines() if ln.startswith("Dialogue")]
    assert "Style: Kara," in ass and len(ev) == 2
    assert ev[0].startswith("Dialogue: 0,") and "\\1a&HFF&" in ev[0] and "\\kf" not in ev[0]
    assert ev[1].startswith("Dialogue: 1,") and "{\\rKara}" in ev[1] and "\\kf" in ev[1] and "{\\rKaraSrc" in ev[1]
    assert "\\kf" not in st.to_ass(segs, {"karaoke": False}, 1280, 720)
    # cắt video / đổi tốc độ: đoạn giọng của câu dời theo
    out = st.remap_after_cut([Segment(5.0, 8.0, "", "x", extra={"kv": [[5.2, 6.0], [6.5, 7.5]]})], [(0, 2), (5, 10)])
    assert out[0].start == 2.0 and out[0].extra["kv"] == [[2.2, 3.0], [3.5, 4.5]]
    out = st.scale_time(out, 2.0)
    assert out[0].extra["kv"] == [[1.1, 1.5], [1.75, 2.25]]


def test_omnivoice_design_presets(monkeypatch, tmp_path):
    from trami import omni_design as od
    from trami import tts

    vocab = {"male", "female", "child", "teenager", "young adult", "middle-aged", "elderly", "very low pitch",
             "low pitch", "moderate pitch", "high pitch", "very high pitch", "whisper"}
    assert all(set(p[3].split(", ")) <= vocab for p in od.PRESETS)          # đúng từ OmniVoice hiểu
    assert len({p[3] for p in od.PRESETS}) == len(od.PRESETS)               # không 2 giọng trùng mô tả
    assert len({p[4] for p in od.PRESETS}) == len(od.PRESETS)
    vs = od.voices()
    assert vs[0]["name"].startswith("⭐") and all(v["id"].startswith("omnid:") for v in vs)
    assert od.get("omnid:ong_lao")["gender"] == "male" and od.get("khong_co") is None
    assert od.key("omnid:nu_tre", "a") != od.key("omnid:nu_tre", "b")
    # tự gán giọng theo nam / nữ
    monkeypatch.setattr(tts, "omnivoice_available", lambda: True)
    asg = tts.auto_assign(["S1", "S2", "S3"], {"tts_engine": "omnivoice"}, {},
                          {"S1": {"g": "male"}, "S2": {"g": "female"}, "S3": {"g": "male"}})
    assert [od.get(asg[k])["gender"] for k in ("S1", "S2", "S3")] == ["male", "female", "male"]
    assert len(set(asg.values())) == 3
    # câu mẫu: tạo 1 lần, lần sau dùng lại; câu mẫu hỏng (im lặng) -> tạo lại với hạt khác
    monkeypatch.setattr(od, "DESIGN_DIR", tmp_path / "design")
    calls = []

    class W:
        def synth(self, text, out, instruct=None, seed=None, **kw):
            calls.append((instruct, seed))
            silent = len(calls) == 1
            tts.write_wav(out, np.zeros(24000, np.float32) if silent else _speech_like(24000, 2.0), 24000)
            return out
    f, text = od.ensure_anchor(W(), "omnid:nam_tre", "repo")
    assert Path(f).exists() and text == od.ANCHOR_TEXT and len(calls) == 2
    assert calls[0] == ("male, young adult, moderate pitch", 1201) and calls[1][1] != 1201
    assert od.ensure_anchor(W(), "omnid:nam_tre", "repo")[0] == f and len(calls) == 2


def test_trim_silence_keeps_soft_word_endings():
    from trami import tts

    sr = tts.SR
    voice = _speech_like(sr, 1.0) * 0.05 / max(1e-6, float(np.abs(_speech_like(sr, 1.0)).max()))   # giọng nhỏ (đỉnh 0,05)
    tail = np.sin(2 * np.pi * 180 * np.arange(int(0.25 * sr)) / sr).astype(np.float32) * 0.006       # cuối chữ rất nhỏ
    a = np.concatenate([np.zeros(int(0.3 * sr), np.float32), voice, tail, np.zeros(int(1.0 * sr), np.float32)])
    out = tts._trim_silence(a)
    assert len(out) >= len(voice) + len(tail)                    # không mất cuối chữ
    assert len(out) <= len(voice) + len(tail) + int(0.2 * sr)    # khoảng lặng dài phía sau vẫn bị bỏ
    assert len(tts._trim_silence(np.zeros(sr, np.float32))) == 0


def test_dub_user_speed_is_minimum(tmp_path):
    """Tốc độ đặt tay = mức tối thiểu: câu vốn phải đọc nhanh hơn (×1,18) giữ tốc độ đó khi đặt ×1,1; đặt ×1,3 thì lên
    ×1,3; bỏ tốc độ -> về như cũ."""
    from trami import tts

    sr = tts.SR
    files = []
    for i in range(3):
        f = tmp_path / f"c{i}.wav"
        tts.write_wav(f, _speech_like(sr, 1.0, seed=i), sr)
        files.append(str(f))
    # câu 0: vừa khít (1 s) · câu 1: chỗ trống ~0,8 s cho 1 s giọng (phải đọc nhanh ~×1,23) · câu 2: rộng
    segs = [Segment(0.0, 1.0, "a", "một", "S1"), Segment(1.15, 1.85, "b", "hai", "S1"), Segment(1.9, 4.5, "c", "ba", "S1")]

    def run(speed):
        for s, f in zip(segs, files):
            s.extra["tts_file"] = f
            if speed is None:
                s.extra.pop("speed", None)
            else:
                s.extra["speed"] = speed
        st = {}
        tts.synthesize_dub(segs, tmp_path / "dub.wav", 6, tmp_path / "w", stats=st, only=set(),
                           settings={"dub_level_match": False}, max_speedup=1.5)
        return [s.extra.get("fit_speed") for s in segs], {int(k): b - a for a, b, k, _f in st["layout"]}

    auto, lay0 = run(None)
    assert auto[0] == 1.0 and 1.15 <= auto[1] <= 1.4 and auto[2] == 1.0
    sp, lay = run(1.1)
    assert sp[0] == 1.1 and sp[2] == 1.1 and sp[1] > 1.12              # câu vốn phải đọc nhanh hơn: không bị hạ xuống ×1,1
    assert lay[0] < lay0[0] - 0.05                                      # câu thường ngắn lại thật
    sp, _ = run(1.3)
    assert sp == [1.3, 1.3, 1.3]                                         # nâng hết lên ×1,3
    assert segs[0].extra["fit"] == "ok" and segs[1].extra["fit"] == "ok"
    sp, lay = run(0.9)
    assert sp[2] == 0.9 and lay[2] > lay0[2] + 0.05                      # còn chỗ -> đọc chậm lại
    assert sp[1] > 1.12                                                  # hết chỗ -> vẫn đọc nhanh cho vừa
    assert run(None)[0] == auto                                          # về tốc độ gốc


def test_spans_and_export_inserts_mute(tmp_path):
    from trami import video
    from trami.audiomix import LiveMix, merge_spans, mute_mask, subtract_spans
    from trami.config import _deep_merge, load_settings
    from trami.project import DEFAULT_EDIT

    assert merge_spans([[3, 4], [1, 2], [1.5, 3.2]]) == [[1.0, 4.0]]
    assert subtract_spans([[0, 10]], [[2, 3], [5, 6]]) == [[0.0, 2.0], [3.0, 5.0], [6.0, 10.0]]
    assert video.out_time(15, [(0, 10), (20, 30)]) == 10 and video.out_time(25, [(0, 10), (20, 30)]) == 15
    t = np.arange(0, 3, 0.01)
    m = mute_mask(t, [[1.0, 2.0]])
    assert m[50] == 1 and m[150] == 0 and m[250] == 1
    mix = LiveMix()
    mix.set_tracks({"orig": np.full((44100 * 3, 2), 10000, np.int16), "dub": np.full((44100 * 3, 2), 5000, np.int16)})
    mix.g, mix.mute = {"orig": 1.0, "dub": 1.0}, [[1.0, 2.0]]
    a, _ = mix.render(int(1.5 * 44100), 1000)
    assert abs(float(a.mean()) - 5000 / 32768) < 1e-3          # trong đoạn tắt: chỉ còn giọng lồng
    # xuất: video 4 s, tắt tiếng gốc 1–2 s, chèn video 1 s (tiếng 1 kHz) tại giây 3
    ff = video.ff
    src, clip = tmp_path / "src.mp4", tmp_path / "clip.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:d=4:r=25", "-f", "lavfi",
                    "-i", "sine=f=440:d=4", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)],
                   check=True)
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=160x120:d=1:r=30", "-f",
                    "lavfi", "-i", "sine=f=1000:d=1", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(clip)], check=True)
    spec = _deep_merge(DEFAULT_EDIT, {"inserts": [{"at": 3.0, "path": str(clip)}], "mute_spans": [[1.0, 2.0]]})
    out = video.build_and_render(src, tmp_path / "out.mp4", spec, load_settings(), tmp_path / "w")
    assert abs(ff.probe(out).duration - 5.0) < 0.15
    pcm = subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(float) / 32768

    def rms(a0, a1):
        return float(np.sqrt(np.mean(x[int(a0 * 8000):int(a1 * 8000)] ** 2)))

    def peak_hz(a0, a1):
        seg = x[int(a0 * 8000):int(a1 * 8000)]
        return int(np.argmax(np.abs(np.fft.rfft(seg))) * 8000 / len(seg))
    assert rms(0.3, 0.8) > 0.02 and rms(1.2, 1.8) < 0.002          # tắt tiếng gốc đúng đoạn
    assert abs(peak_hz(3.2, 3.8) - 1000) < 20                      # đoạn chèn giữ tiếng của nó
    assert abs(peak_hz(4.2, 4.8) - 440) < 20                       # sau đoạn chèn là video gốc tiếp


def test_no_console_streams_keep_progress_bars_working(tmp_path, monkeypatch):
    """Mở tool bằng pythonw (shortcut / ChayTool_Windows.bat, không cửa sổ đen): sys.stdout, sys.stderr = None ->
    thanh tiến độ tqdm của bộ tách giọng từng báo "'NoneType' object has no attribute 'write'"."""
    tqdm = pytest.importorskip("tqdm").tqdm
    import logging

    from trami.config import fix_std_streams, no_console
    from trami.progress_hook import tqdm_progress

    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        monkeypatch.setattr(sys, name, None)
    with pytest.raises(AttributeError):                      # đúng lỗi người dùng gặp
        for _ in tqdm(range(3)):
            pass
    log = tmp_path / "logs" / "console.txt"
    fix_std_streams(log)
    assert sys.stdout is sys.stderr and no_console()
    seen = []
    with tqdm_progress(lambda x, m: seen.append(m), 0.15, 0.97, "Đang tách giọng – đoạn"):
        for _ in tqdm(range(5), mininterval=0):
            pass
    print("xin chào")
    logging.getLogger("trami-test").addHandler(logging.StreamHandler())
    logging.getLogger("trami-test").warning("cảnh báo")
    sys.stderr.flush()
    text = log.read_text("utf-8")
    assert "5/5" in text and "xin chào" in text and "cảnh báo" in text
    assert seen and seen[-1].endswith("5/5")
    fix_std_streams(tmp_path / "khac.txt")                   # gọi lại không tạo file mới
    assert not (tmp_path / "khac.txt").exists()


def test_dub_overflow_modes_single_long_line():
    """1 câu đọc dài hơn chỗ trống (3,2 s cho 2 s) – mỗi cách xếp xử lý đúng như mô tả."""
    from trami.tts import plan_layout

    items = [dict(k=0, want=0.5, dur=1.0), dict(k=1, want=4.0, dur=3.2), dict(k=2, want=6.0, dur=1.2),
             dict(k=3, want=9.0, dur=1.0)]

    def run(mode, **kw):
        return plan_layout(items, 12, mode=mode, max_speedup=1.3, **kw)

    r = run("smart")                       # tăng tốc nhẹ ×1,15 + đọc sớm phần còn thiếu, câu sau đúng giờ
    assert abs(r[1]["speed"] - 1.15) < 0.01 and -1.0 <= r[1]["shift"] < -0.3 and r[2]["shift"] == 0
    assert r[1]["end"] <= 6.0 and r[1]["fit"] == "fast"
    r = run("push")                        # tăng tốc tối đa rồi câu sau lùi lại, không chồng
    assert r[1]["speed"] == 1.3 and r[1]["shift"] == 0 and r[2]["shift"] > 0.4 and r[2]["start"] >= r[1]["end"]
    assert r[1]["fit"] == "long" and r[1]["over"] == 0
    r = run("overlap")                     # câu sau vẫn đúng giờ, đọc đè lên
    assert r[2]["start"] == 6.0 and r[1]["over"] > 0.4 and r[1]["end"] > 6.4
    r = run("early")                       # bắt đầu sớm hơn để câu sau vào đúng giờ, không cần tăng tốc
    assert r[1]["speed"] == 1.0 and r[1]["shift"] < -1.1 and r[1]["end"] <= 6.0 and r[2]["shift"] == 0
    r = run("cut")                         # cách cũ: cắt bớt cuối câu
    assert r[1]["keep"] is not None and r[1]["fit"] == "long" and r[2]["shift"] <= 0.25 + 1e-6
    r = plan_layout(items, 12, mode="push", max_speedup=1.3)
    items[1]["mode"] = "overlap"           # cách xếp riêng câu này thắng cách xếp chung
    r2 = plan_layout(items, 12, mode="push", max_speedup=1.3)
    assert r[2]["shift"] > 0.4 and r2[2]["shift"] == 0 and r2[1]["over"] > 0.4
    items[1]["fixed"] = True               # đoạn giọng người dùng kéo tay: không bao giờ đọc sớm
    assert plan_layout(items, 12, mode="early", max_speedup=1.3)[1]["start"] >= 4.0


def test_dub_overflow_cluster_of_long_lines():
    """5 câu dài liền nhau (3 s giọng cho mỗi 2 s): Tự động dùng 1 tốc độ chung cho cả cụm, trễ không quá mức cho
    phép và đuổi kịp ở chỗ nghỉ; Nối tiếp thì trễ dồn lại rồi đuổi kịp; không câu nào đảo thứ tự."""
    from trami.tts import plan_layout

    items = [dict(k=i, want=2.0 + 2.0 * i, dur=3.0) for i in range(5)] + [dict(k=5, want=14.0, dur=1.0)]
    r = plan_layout(items, 20, mode="smart", max_speedup=1.3, lead_max=1.0, max_lag=2.0)
    speeds = {round(r[i]["speed"], 3) for i in range(5)}
    assert len(speeds) == 1                                        # cả cụm cùng 1 tốc độ
    assert max(r[i]["shift"] for i in range(6)) <= 2.0 and r[5]["shift"] == 0
    starts = [r[i]["start"] for i in range(6)]
    assert starts == sorted(starts) and all(r[i]["end"] <= r[i + 1]["start"] + 1e-6 for i in range(5))
    r = plan_layout(items, 20, mode="push", max_speedup=1.3)
    lags = [r[i]["shift"] for i in range(5)]
    assert lags == sorted(lags) and lags[-1] > 1.0 and r[5]["shift"] == 0     # trễ dồn, đuổi kịp ở chỗ nghỉ
    # cụm rất dài: Tự động không để trễ quá 2 s – câu sau vào đúng mức đó và đọc đè
    many = [dict(k=i, want=2.0 + 2.0 * i, dur=4.0) for i in range(8)]
    r = plan_layout(many, 30, mode="smart", max_speedup=1.3, max_lag=2.0)
    assert max(x["shift"] for x in r.values()) <= 2.0 + 1e-6 and any(x["over"] > 0.5 for x in r.values())
    assert [r[i]["start"] for i in range(8)] == sorted(r[i]["start"] for i in range(8))


def test_dub_overlap_mix_and_subtitles_follow_voice(tmp_path):
    """Đọc đè: 2 giọng cộng lại không vỡ tiếng (nén mềm); mỗi câu ghi đoạn giọng đang phát (dub_at), độ lệch, phần đè;
    phụ đề chạy theo giọng lồng: câu bị lùi hiện đúng lúc giọng đọc, không đè câu sau."""
    from trami import tts
    from trami.subtitle import follow_dub

    sr = tts.SR
    files = []
    for i, d in enumerate((1.0, 3.0, 1.0)):
        f = tmp_path / f"c{i}.wav"
        tts.write_wav(f, np.clip(_speech_like(sr, d, seed=i) * 4.0, -0.95, 0.95), sr)
        files.append(str(f))
    segs = [Segment(0.2, 1.2, "a", "một", "S1"), Segment(2.0, 3.8, "b", "hai", "S2"), Segment(4.0, 5.0, "c", "ba", "S1")]
    for s, f in zip(segs, files):
        s.extra["tts_file"] = f
    st = {}
    tts.synthesize_dub(segs, tmp_path / "o.wav", 7, tmp_path / "w", stats=st, only=set(), max_speedup=1.0,
                       settings={"dub_overflow": "overlap", "dub_level_match": False})
    a = tts._decode(tmp_path / "o.wav")
    assert float(np.abs(a).max()) < 1.0
    assert segs[1].extra["fit_over"] > 0.5 and segs[1].extra["fit"] == "long" and st["shift"]["over"] == 1
    assert abs(segs[2].extra["dub_at"][0] - 4.0) < 0.01                       # câu sau vẫn đúng giờ
    tts.synthesize_dub(segs, tmp_path / "o.wav", 7, tmp_path / "w", stats=st, only=set(), max_speedup=1.0,
                       settings={"dub_overflow": "push", "dub_level_match": False})
    lag = segs[2].extra["fit_shift"]
    assert lag > 0.5 and st["shift"]["late"] == 1 and "fit_over" not in segs[1].extra
    out = follow_dub(segs)
    assert abs(out[2].start - segs[2].extra["dub_at"][0]) < 1e-6 and out[2].end > segs[2].extra["dub_at"][1]
    assert out[1].end <= out[2].start and out[0].start == segs[0].start        # câu đúng giờ giữ nguyên
    assert segs[2].start == 4.0                                                # không đổi thời gian gốc


def test_realign_split_drops_whole_line_voice(monkeypatch):
    """Căn khớp thoại tách 1 câu đã lồng thành 2: 2 nửa KHÔNG được giữ giọng của cả câu cũ (trước đây xếp lại -> đặt
    giọng đó 2 lần); câu không bị tách giữ giọng; đã có bản lồng -> tự xếp lại theo mốc mới, không đọc lại câu nào."""
    from trami import pipeline
    from trami.project import Project

    p = Project.create("realign_dub")
    p.data["info"] = {"duration": 10}
    p.segments = [Segment(1.0, 3.0, "你好", "Xin chào", "S1", {"tts_file": "a.wav", "fit": "long", "dub_at": [1, 3]}),
                  Segment(4.0, 7.0, "我们走吧快点", "Đi thôi nhanh lên", "S1",
                          {"tts_file": "b.wav", "fit": "ok", "speed": 1.2, "dub_at": [4, 6]})]
    p.save()
    (p.work).mkdir(parents=True, exist_ok=True)

    def fake_align(segs, wav, lang, settings, progress, cancel, engine=""):
        a, b = segs
        return [Segment(1.3, 2.4, a.text, a.trans, a.speaker, dict(a.extra)),          # chỉ dời mốc
                Segment(4.2, 5.0, "我们走吧", "", b.speaker, dict(b.extra)),          # tách đôi
                Segment(5.6, 6.5, "快点", "", b.speaker, dict(b.extra))]
    monkeypatch.setattr(pipeline, "align_segments", fake_align)
    monkeypatch.setattr(pipeline.ff, "run", lambda *a, **k: Path(a[0][-1]).write_bytes(b"x" * 2000))
    relaid = []
    monkeypatch.setattr(pipeline, "relayout", lambda *a, **k: relaid.append(1))
    monkeypatch.setattr(pipeline, "has_dub", lambda q: True)
    assert pipeline.realign(p) == 3
    s = Project(p.id).segments
    assert s[0].extra.get("tts_file") == "a.wav" and "fit" not in s[0].extra and "dub_at" not in s[0].extra
    assert all("tts_file" not in x.extra and "dub_at" not in x.extra for x in s[1:])
    assert s[1].extra.get("speed") == 1.2           # tốc độ đặt tay vẫn giữ
    assert relaid == [1]


def test_moved_source_video_found_or_relinked(sample_video, tmp_path, monkeypatch):
    """Video gốc bị chuyển chỗ: tự tìm lại file cùng tên + cùng độ dài (Downloads…), chọn lại tay thì giữ nguyên phụ đề;
    chọn nhầm video khác độ dài -> báo lỗi; FFmpeg báo 'không tìm thấy file' dễ hiểu thay vì lỗi khó hiểu."""
    from trami import pipeline
    from trami.project import Project

    old_dir, home = tmp_path / "cu", tmp_path / "home"
    old_dir.mkdir()
    (home / "Downloads" / "phim").mkdir(parents=True)
    src = old_dir / "42788455331-1-192.mp4"
    shutil.copy(sample_video, src)
    p = Project.create("mat_video")
    pipeline.attach_source(p, src)
    p.segments = [Segment(1, 2, "你好", "Xin chào")]
    p.save()
    moved = home / "Downloads" / "phim" / src.name
    src.replace(moved)
    assert pipeline.source_missing(p)
    with pytest.raises(ff.FFmpegError, match="Không tìm thấy file"):
        pipeline.make_preview(p)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert pipeline.find_moved_source(p) == moved
    other = tmp_path / "khac.m4a"
    _fake_audio(other, 2.0)
    with pytest.raises(ValueError, match="không phải cùng"):
        pipeline.relink_source(p, other)
    pipeline.relink_source(p, moved)
    q = Project(p.id)
    assert not pipeline.source_missing(q) and q.source == moved.resolve() and q.segments[0].trans == "Xin chào"


def test_translate_only_chosen_rows_without_cache(monkeypatch):
    """Dịch ở tab Lồng tiếng: chỉ dịch đúng các câu chọn; dịch LẠI thì không lấy bản dịch cũ đã nhớ."""
    from trami import pipeline, translate
    from trami.project import Project

    calls = []

    def fake(self, lines, progress=None, cancel=None, summary=""):
        calls.append(list(lines))
        return [f"mới {len(calls)}" for _ in lines]
    monkeypatch.setattr(translate.Translator, "translate_lines", fake)
    p = Project.create("dich_cau_chon")
    p.segments = [Segment(0, 1, "你好", "Xin chào"), Segment(2, 3, "走吧", ""), Segment(4, 5, "等等", "Đợi đã")]
    p.data["cache"] = {"google|auto|你好": "Xin chào"}
    p.save()
    pipeline.translate(p, "google", "auto", True, {"translate_engine": "google"}, rows=None)
    assert [s.trans for s in p.segments] == ["Xin chào", "mới 1", "Đợi đã"] and calls == [["走吧"]]
    pipeline.translate(p, "google", "auto", False, {"translate_engine": "google"}, rows=[0])
    assert p.segments[0].trans == "mới 2" and p.segments[2].trans == "Đợi đã" and calls[-1] == ["你好"]


def test_target_language_detect_and_translate(monkeypatch):
    """Dịch sang Anh / Thái / Nhật / Hàn: nhận tiếng theo chữ viết, lời nhắc AI, Google đúng tiếng, bộ nhớ dịch riêng,
    đổi tiếng đích thì dịch lại cả bản, bảng thuật ngữ Hán Việt không dùng cho tiếng khác."""
    from trami import langs, pipeline, translate
    from trami.project import Project

    assert langs.detect("Hello there", "en") == "en" and langs.detect("Hello there", "vi") == "vi"
    assert langs.detect("Xin chào bạn", "en") == "vi" and langs.detect("สวัสดีครับ") == "th"
    assert langs.detect("こんにちは", "vi") == "ja" and langs.detect("大丈夫", "ja") == "ja"
    assert langs.detect("안녕하세요") == "ko" and langs.detect("你好", "vi") == "vi"
    assert langs.units("สวัสดีครับ", "th") == 7          # 10 ký tự, 3 dấu ั ี ั không tính riêng
    assert langs.units("I am fine, thanks", "en") == 4 and langs.budget(2.0, "th") > langs.budget(2.0, "en")
    assert langs.edge_voice("en", "male") == "edge:en-US-GuyNeural"
    assert translate.system_prompt("vi") == translate.SYSTEM_PROMPT
    sp = translate.system_prompt("ko")
    assert "Korean" in sp and "Korean syllables" in sp and "Hán" not in sp
    assert translate.untranslated("你好世界", "ja") and not translate.untranslated("大丈夫です", "ja")
    assert translate.untranslated("你好", "en") and not translate.untranslated("Hello", "en")
    k = translate.cache_key("google", "auto", "th", "你好")
    assert k == "google|auto|th|你好" and translate.cache_line(k) == "你好"
    assert translate.cache_key("google", "auto", "vi", "你|好") == "google|auto|你|好"
    assert translate.cache_line("google|auto|你|好") == "你|好"
    # Google: nguồn / đích đúng tiếng
    sent = {}

    class R:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return [[["Hello", "你好"]]]

    def fake_post(url, params=None, **kw):
        sent.update(params or {})
        return R()
    from trami import net

    monkeypatch.setattr(net, "session", lambda: type("S", (), {"post": staticmethod(fake_post)})())
    tr = translate.Translator({"translate_engine": "google"}, target="ja")
    assert tr._gl() == ("zh-CN", "ja") and tr._gtx("你好") == "Hello" and sent["tl"] == "ja" and sent["sl"] == "zh-CN"
    # dự án: đổi tiếng đích -> dịch lại mọi câu (kể cả câu đã có bản tiếng Việt), thuật ngữ theo tiếng
    seen = []

    def fake(self, lines, progress=None, cancel=None, summary=""):
        seen.append((self.target, dict(self.glossary), list(lines)))
        return [f"EN {x}" for x in lines]
    monkeypatch.setattr(translate.Translator, "translate_lines", fake)
    p = Project.create("dich_sang_anh")
    p.segments = [Segment(0, 1, "李逍遥", "Lý Tiêu Dao"), Segment(2, 3, "走吧", "")]
    p.data["glossary"] = "李逍遥=Lý Tiêu Dao"
    p.data["glossary_en"] = "李逍遥=Li Xiaoyao"
    p.data["target_lang"] = "en"
    p.save()
    assert pipeline.target_lang(p, {}) == "en" and pipeline.trans_lang(p) == "vi"
    pipeline.translate(p, "google", "auto", True, {"translate_engine": "google"})
    tgt, gl, lines = seen[-1]
    assert tgt == "en" and gl == {"李逍遥": "Li Xiaoyao"} and lines == ["李逍遥", "走吧"]
    assert [s.trans for s in p.segments] == ["EN 李逍遥", "EN 走吧"] and p.data["trans_lang"] == "en"
    assert pipeline.export_subs(p, "srt", "trans", {}).name.endswith(".en.srt")
    # nạp SRT đã dịch tiếng Nhật vào dự án đang dịch sang tiếng Anh -> "Dịch sang" đổi theo (bấm Dịch không dịch đè)
    cues = [Segment(0, 1, "李さん、こんにちは"), Segment(2, 3, "行こう")]
    segs = p.segments
    pipeline.apply_translation(segs, cues)
    assert segs[0].trans == "李さん、こんにちは"
    p.segments = segs
    p.save()
    assert pipeline.note_trans_lang(p) == "ja" and p.data["target_lang"] == "ja" and pipeline.trans_lang(p) == "ja"
    assert pipeline.note_trans_lang(p) is None
    # auto(): tuỳ chọn "Dịch sang" ghi vào dự án
    p2 = Project.create("auto_target")
    monkeypatch.setattr(pipeline, "translate", lambda *a, **k: 0)
    monkeypatch.setattr(pipeline, "export_subs", lambda *a, **k: Path("x.srt"))
    p2.segments = [Segment(0, 1, "你好", "")]
    p2.save()
    pipeline.auto(p2, {"target": "th", "render": False})
    assert p2.data["target_lang"] == "th"
    p.delete()
    p2.delete()


def test_multilang_dub_voices(monkeypatch, tmp_path):
    """Giọng đọc theo tiếng của từng câu: Edge đổi đúng giọng tiếng đó (cùng nam / nữ), VieNeu chỉ tiếng Việt ->
    câu tiếng khác đọc bằng Edge; OmniVoice nhận tham số language + câu mẫu cùng tiếng."""
    from trami import omni_design as od
    from trami import tts

    got = {}

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            got[i] = (voice, text)
            _fake_audio(p, 0.6)
        return []
    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    monkeypatch.setattr(tts, "vieneu_available", lambda: True)
    monkeypatch.setattr(tts, "vieneu_presets", lambda mode="v3turbo": [{"id": "vieneu:Binh", "name": "Bình",
                                                                         "gender": "male"}])
    monkeypatch.setattr(tts, "vieneu_sample_voices", lambda: [])
    segs = [Segment(0, 1, "a", "Hello there", "S1"), Segment(1.5, 2.5, "b", "Xin chào bạn", "S1"),
            Segment(3, 4, "c", "สวัสดีครับ", "S1"), Segment(4.5, 5.5, "d", "こんにちは、元気？", "S1"),
            Segment(6, 7, "e", "안녕하세요", "S1"), Segment(7.5, 8.5, "f", "See you", "S2")]
    stats = {}
    tts.synthesize_dub(segs, tmp_path / "dub.wav", 9.0, tmp_path / "t", voice="edge:vi-VN-HoaiMyNeural",
                       voices={"S2": "vieneu:Binh"}, stats=stats, lang="en")
    assert [got[i][0] for i in range(6)] == ["en-US-AriaNeural", "vi-VN-HoaiMyNeural", "th-TH-PremwadeeNeural",
                                             "ja-JP-NanamiNeural", "ko-KR-SunHiNeural", "en-US-ChristopherNeural"]
    assert got[2][1] == "สวัสดีครับ" and got[3][1] == "こんにちは、元気？" and got[4][1] == "안녕하세요"
    assert stats["lang_edge"] == 1 and stats["placed"] == 6 and stats["langs"]["en"] == 2
    assert tts.clean_tts_text("你好 hello", "vi") == "hello" and tts.clean_tts_text("ไทย 123", "th") == "ไทย 123"
    vs = tts.list_voices({}, "en")
    assert vs and all(not v["id"].startswith(("vieneu", "clone")) for v in vs)
    assert any(v["id"] == "edge:en-US-EmmaNeural" for v in vs)
    asg = tts.auto_assign(["S1", "S2"], {"tts_engine": "vieneu"}, {}, {"S1": {"g": "male"}, "S2": {"g": "female"}},
                          "ko")
    assert asg == {"S1": "edge:ko-KR-InJoonNeural", "S2": "edge:ko-KR-SunHiNeural"}
    with pytest.raises(ValueError):
        tts.preview_voice("vieneu:Binh", "Hello", tmp_path / "pv", {}, None, "en")
    # OmniVoice giọng chọn sẵn: câu mẫu theo tiếng, file riêng, không xoá nhầm giọng khác / tiếng khác
    monkeypatch.setattr(od, "DESIGN_DIR", tmp_path / "design")
    calls = []

    class W:
        def synth(self, text, out, instruct=None, seed=None, language=None, **kw):
            calls.append((text, language))
            tts.write_wav(out, _speech_like(24000, 2.0), 24000)
            return out
    fv, _ = od.ensure_anchor(W(), "omnid:nu_trung_nien_tram", "repo")
    f_vi, t_vi = od.ensure_anchor(W(), "omnid:nu_trung_nien", "repo")
    f_en, t_en = od.ensure_anchor(W(), "omnid:nu_trung_nien", "repo", "en")
    assert t_en == od.ANCHOR_TEXTS["en"] and calls[-1] == (od.ANCHOR_TEXTS["en"], "en") and calls[0][1] == "vi"
    assert f_en != f_vi and all(Path(x).exists() for x in (fv, f_vi, f_en))
    assert od.key("omnid:nu_tre", "r", "vi") == od.key("omnid:nu_tre", "r")


def test_subtitle_font_wrap_karaoke_other_languages():
    """Phụ đề tiếng Thái / Nhật / Hàn: font có đủ chữ (\\fn theo câu), ngắt dòng tiếng viết liền, karaoke từng chữ."""
    from trami import langs
    from trami.karaoke import tokens

    stl = {"font": "Be Vietnam Pro", "lang": "ja"}
    assert st.line_font("こんにちは", stl) == langs.subtitle_font("ja", "Be Vietnam Pro")
    assert st.line_font("大丈夫", stl) == langs.subtitle_font("ja", "Be Vietnam Pro")
    assert st.line_font("Xin chào", {"font": "Bangers", "lang": "vi"}) == "Bangers"
    assert st.line_font("你好", {"font": "Bangers", "lang": "vi"}) == st.cjk_font_name({})
    segs = [Segment(0, 1, "a", "สวัสดีครับ"), Segment(1, 2, "b", "Xin chào"), Segment(2, 3, "c", "안녕하세요")]
    ass = st.to_ass(segs, {"font": "Be Vietnam Pro", "lang": "vi"}, 1920, 1080)
    assert "{\\fn" + langs.subtitle_font("th", "x") + "}สวัสดีครับ" in ass
    assert "{\\fn" + langs.subtitle_font("ko", "x") + "}" in ass and "\\fnBe Vietnam Pro" not in ass
    kara = st.to_ass(segs[:1], {"font": "Be Vietnam Pro", "karaoke": True, "box": True}, 1920, 1080)
    assert kara.count("\\fn" + langs.subtitle_font("th", "x")) == 2      # lớp nền + lớp chữ karaoke
    ja = "今日はとても面白いお話をしたいと思います、ゆっくり座って聞いてくださいね。"
    w = st.wrap_line(ja, 30)
    a, b = w.split("\n")
    assert a + b == ja and abs(st.vis_len(a) - st.vis_len(b)) < 12 and b[0] not in "、。ー"
    th = "วันนี้ฉันจะเล่าเรื่องที่น่าสนใจมากให้ฟังนั่งลงสบายๆแล้วตั้งใจฟังนะ"
    a, b = st.wrap_line(th, 30).split("\n")
    assert a + b == th and not __import__("unicodedata").category(b[0]) == "Mn" and a[-1] not in "เแโใไ"
    assert st.wrap_line("Xin chào các bạn", 42) == "Xin chào các bạn"
    toks = tokens("สวัสดี")
    assert [t for t, _ in toks] == ["ส", "วั", "ส", "ดี"]


def test_subtitle_line_fit_and_rounded_box():
    """Xuống dòng theo bề rộng chữ THẬT (không đoán số ký tự), thu nhỏ chữ cho vừa 1 dòng, nền bo góc tự vẽ."""
    from trami import textfit

    s = "Hôm nay mình sẽ kể cho các bạn nghe một câu chuyện thật thú vị nhé các bạn ơi"
    w = textfit.width(s, "Be Vietnam Pro", True, 30)
    assert 600 < w < 1000                          # ~10 px / ký tự ở cỡ 30 (cách cũ đoán 16,5 px)
    assert textfit.fit_lines(s, "Be Vietnam Pro", True, 30, 0.9 * 1080) == ([s], 30)   # video dọc 1080: vừa 1 dòng
    lines, fs = textfit.fit_lines(s, "Be Vietnam Pro", True, 30, 0.9 * 720)
    assert len(lines) == 2 and fs == 30 and " ".join(lines) == s
    one, fs2 = textfit.fit_lines(s, "Be Vietnam Pro", True, 30, 0.9 * 720, mode="shrink", min_scale=0.6)
    assert one == [s] and 18 <= fs2 < 30
    lines3, fs3 = textfit.fit_lines(s * 3, "Be Vietnam Pro", True, 30, 0.9 * 720, max_lines=2)
    assert len(lines3) == 2 and fs3 < 30          # dài quá 2 dòng: chữ tự nhỏ lại
    ja = "今日はとても面白いお話をしたいと思います、ゆっくり座って聞いてくださいね。"
    a, b = textfit.fit_lines(ja, "Noto Sans CJK JP", True, 40, 700)[0]
    assert a.endswith("、")                        # ưu tiên ngắt sau dấu câu
    seg = [Segment(0, 2, "a", s)]

    def style_fields(ass):
        return ass.split("Style: Default,")[1].split("\n")[0].split(",")
    shrink = st.to_ass(seg, {"size": 30, "line_fit": "shrink", "min_scale": 0.6}, 720, 1280)
    assert "{\\fs" in shrink and "\\N" not in shrink.split("[Events]")[1]
    box = st.to_ass(seg, {"size": 30, "box": True, "box_radius": 0.3}, 720, 1280)
    ev = box.split("[Events]")[1]
    assert "\\p1}m " in ev and " b " in ev and "Dialogue: 1," in ev      # nền vẽ bo góc ở lớp 0, chữ lớp 1
    f = style_fields(box)
    assert f[14] == "1" and float(f[15]) == 0.0                         # chữ không viền (nền đã vẽ riêng)
    block = st.layout_event([(s, "vi")], {"size": 30, "box": True, "box_block": True}, 720, 1280)
    hug = st.layout_event([(s, "vi")], {"size": 30, "box": True, "box_radius": 0.2}, 720, 1280)
    assert len(block["boxes"]) == 1 and len(hug["boxes"]) == 2
    (x1, y1, w1, h1), (x2, y2, w2, h2) = hug["boxes"]
    assert abs((y1 + h1) - y2) < 1e-6              # nền từng dòng nối liền, không chồng (chỗ chồng sẽ đậm hơn)
    assert st.rounded_rect_path(0, 0, 100, 40, 0) == "m 0 0 l 100 0 100 40 0 40"
    assert style_fields(st.to_ass(seg, {"size": 30, "line_width": 1.0}, 720, 1280))[18:20] == ["0", "0"]  # lề 0
    assert style_fields(st.to_ass(seg, {"size": 30}, 720, 1280))[18:20] == ["36", "36"]  # mặc định 90 %
    legacy = st.to_ass(seg, {"size": 30, "box": True}, 720, 1280)
    assert "\\p1" not in legacy and "\\xbord" in legacy                   # nền vuông cũ giữ nguyên cách làm


def test_blur_box_rounded_feather_export(sample_video, tmp_path):
    """Làm mờ / dải che bo góc + viền mờ dần: mặt nạ khi xuất (FFmpeg geq) khớp mặt nạ xem trước (numpy)."""
    from trami import video
    from trami.config import load_settings

    m = video.round_mask(200, 60, 30, 6)
    assert m[30, 100] == 1.0 and m[0, 0] == 0.0 and 0 < m[30, 2] < 1      # giữa đặc, góc trống, mép mờ dần
    assert "gblur" in video.blur_filter({"blur_kind": "gauss", "blur_strength": 20}, 200, 60)
    assert "neighbor" in video.blur_filter({"blur_kind": "pixel", "blur_strength": 20}, 200, 60)
    assert "lutyuv" in video.blur_filter({"darken": 0.3}, 200, 60)
    hs = {"mode": "box", "box_color": "#FF0000", "box_opacity": 1.0, "round": 0.5, "feather": 0.0,
          "regions": [{"x": 0.2, "y": 0.6, "w": 0.6, "h": 0.3}]}
    out = tmp_path / "o.mp4"
    video.build_and_render(sample_video, out, {"hardsub": hs, "subtitles": {"burn": False}, "dub": {}},
                           load_settings(), tmp_path / "w", [], None, None)
    raw = subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-ss", "0.5", "-i", str(out), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True).stdout
    info = ff.probe(out)
    a = np.frombuffer(raw, np.uint8).reshape(info.height, info.width, 3).astype(int)
    W, H = info.width, info.height
    x0, y0, w, h = int(0.2 * W), int(0.6 * H), int(0.6 * W), int(0.3 * H)
    cx, cy = x0 + w // 2, y0 + h // 2
    assert a[cy, cx, 0] > 200 and a[cy, cx, 1] < 60                     # giữa dải: đỏ
    assert not (a[y0 + 1, x0 + 1, 0] > 200 and a[y0 + 1, x0 + 1, 1] < 60)   # góc đã bo: không đỏ


def test_temporal_fill_uses_real_background():
    """Xoá chữ bằng nền thật từ khung khác: máy quay lia (bù chuyển động) và đứng yên (lấp suốt câu dài), không
    nhấp nháy; chỗ chưa từng lộ ra mới để vẽ."""
    from trami.vfill import BandFiller

    rng = np.random.default_rng(3)
    import cv2

    tex = cv2.GaussianBlur((rng.random((90, 900, 3)) * 255).astype(np.float32), (0, 0), 1.5)
    drawn = []

    def fb(img, m):
        drawn.append(int((m > 0).sum()))
        img[m > 0] = 0

    f = BandFiller(fb, lookahead=40, ctx_rows=[(0, 25), (65, 90)])
    frames, masks, truth = [], [], []
    for i in range(120):
        x = min(i * 3, 180) if i < 60 else 180          # lia 3 px/khung rồi đứng yên
        bg = tex[:, x:x + 600].copy()
        m = np.zeros((90, 600), bool)
        if (i // 20) % 2 == 0:                          # chữ hiện 20 khung, tắt 20 khung
            m[35:55, 150 + (i // 40) * 60:420 + (i // 40) * 60] = True
        img = bg.copy()
        img[cv2.erode(m.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0] = 255   # mặt nạ nới rộng hơn chữ như thật
        frames.append(img.astype(np.uint8))
        masks.append(m)
        truth.append(bg)
    outs = {}
    for i, (img, m) in enumerate(zip(frames, masks)):
        f.push(i, img, m)
        outs.update(dict(f.pop()))
    outs.update(dict(f.pop(eof=True)))
    errs = [np.abs(outs[i].astype(float) - truth[i])[masks[i]].mean() for i in range(120) if masks[i].any()]
    assert len(outs) == 120 and max(errs) < 6.0, (max(errs), np.mean(errs))
    assert f.stats["temporal"] >= len(errs) - 2 and sum(drawn) == 0
    flick = np.abs(outs[91].astype(float) - outs[90].astype(float))[masks[90]].mean()   # đứng yên, cùng câu
    assert flick < 1.0


def test_fixed_logo_removed_by_unblending(tmp_path):
    """Gỡ logo cố định: tính ngược lớp logo trong suốt từ nhiều khung -> nền thật hiện lại."""
    from trami import logo

    D = tmp_path
    ffm = ff.ffmpeg_bin()
    subprocess.run([ffm, "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "mandelbrot=s=320x180:rate=25:end_scale=0.0005,hue=h=t*40", "-t", "8", "-c:v", "libx264",
                    "-crf", "12", "-pix_fmt", "yuv420p", str(D / "bg.mp4")], check=True)
    subprocess.run([ffm, "-loglevel", "error", "-y", "-i", str(D / "bg.mp4"), "-vf",
                    "drawbox=x=220:y=12:w=80:h=26:color=white@0.55:t=fill", "-c:v", "libx264", "-crf", "12",
                    "-pix_fmt", "yuv420p", str(D / "v.mp4")], check=True)
    rect = {"x": 212 / 320, "y": 6 / 180, "w": 96 / 320, "h": 38 / 180}
    m = logo.get_model(D / "v.mp4", rect, 320, 180, 8.0, D)
    assert m.mask.sum() > 0.7 * 80 * 26 and m.opaque.sum() < 0.1 * m.mask.sum()
    assert abs(float((1 - m.beta[..., 0])[16:36, 16:84].mean()) - 0.55) < 0.12     # độ đục đo được ≈ 0,55
    assert logo.model_path(D, D / "v.mp4", rect).exists()                           # lưu lại, lần sau dùng luôn

    def frame(path, t):
        b = subprocess.run([ffm, "-loglevel", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f",
                            "rawvideo", "-pix_fmt", "bgr24", "pipe:1"], capture_output=True).stdout
        return np.frombuffer(b, np.uint8).reshape(180, 320, 3).copy()
    for t in (1.5, 5.5):
        B, V = frame(D / "bg.mp4", t).astype(float), frame(D / "v.mp4", t)
        sel = ~m.opaque[12:38, 220 - m.x0:300 - m.x0] if m.y0 == 0 else np.ones((26, 80), bool)
        before = np.abs(V[12:38, 220:300].astype(float) - B[12:38, 220:300])[sel].mean()
        m.apply(V)
        after = np.abs(V[12:38, 220:300].astype(float) - B[12:38, 220:300])[sel].mean()
        assert after < before / 4, (before, after)


def test_hf_download_progress_and_xet_fallback(tmp_path, monkeypatch):
    """Tải HuggingFace: thanh tqdm riêng luôn đếm (kể cả không có cửa sổ đen); Xet đứng im -> tải thường + ghi nhớ."""
    from trami import config, hf_download as hd

    pytest.importorskip("huggingface_hub")
    from huggingface_hub.utils.tqdm import _create_progress_bar

    stats = hd.Stats()
    Track = hd.tracker(stats)
    files = Track(total=3, desc="Fetching 3 files")
    net = _create_progress_bar(cls=Track, log_level=30, name="x", desc="Downloading bytes", total=0, unit="B",
                               unit_scale=True)
    rec = _create_progress_bar(cls=Track, log_level=30, name="x", desc="Reconstructing", total=0, unit="B",
                               unit_scale=True)
    rec.total = 5_000_000
    rec.update(2_000_000)
    net.update(1_500_000)
    files.update(2)
    s = stats.snapshot()
    assert s == {"files": [2, 3], "bytes": 2_000_000, "total": 5_000_000, "net": 1_500_000}

    monkeypatch.setattr(config, "NO_XET_FILE", tmp_path / "hf_no_xet.txt")
    monkeypatch.setattr(config, "PREFER_MS_FILE", tmp_path / "prefer_ms.txt")
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.setattr(hd, "xet_enabled", lambda: not (tmp_path / "hf_no_xet.txt").exists())
    monkeypatch.setattr(config, "installed", lambda *a: True)
    calls = []

    def src(req, env):
        return "ms" if req.get("ms_id") else ("http" if env.get("HF_HUB_DISABLE_XET") else "xet")

    # Xet đứng im -> tải thường cũng đứng (0 byte: mạng chặn máy chủ Xet) -> ModelScope chính hãng tải được
    def fake_run(req, env, limit, progress, cancel, label, lo, hi):
        calls.append(src(req, env))
        if req.get("ms_id"):
            assert req["ms_id"] == "Tencent-Hunyuan/Hy-MT2-1.8B"
            return "ok", str(tmp_path), 5_000_000
        return "stall", "không nhận thêm dữ liệu", 0
    monkeypatch.setattr(hd, "_run", fake_run)
    msgs = []
    repo = "tencent/Hy-MT2-1.8B"
    assert hd.download(repo, progress=lambda x, m: msgs.append(m)) == tmp_path
    assert calls == ["xet", "http", "ms"]
    assert (tmp_path / "hf_no_xet.txt").exists() and (tmp_path / "prefer_ms.txt").exists()
    assert any("chuyển sang tải thường" in m for m in msgs) and any("ModelScope" in m for m in msgs)
    assert os.environ.get("HF_HUB_DISABLE_XET") == "1"
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    calls.clear()
    hd.download(repo)                       # lần sau: ModelScope trước
    assert calls == ["ms"]

    # tải thường nhận được dữ liệu rồi mới đứng -> thử lại 1 lần (nối tiếp); không có bản ModelScope -> báo lỗi tổng hợp
    monkeypatch.setattr(hd, "_run", lambda req, env, *a: calls.append(src(req, env)) or ("stall", "đứng im", 2_000_000))
    calls.clear()
    with pytest.raises(RuntimeError, match="Không tải được"):
        hd.download("a/b")
    assert calls == ["http", "http"]
    # cần token / đồng ý điều khoản -> báo ngay, không thử lại
    monkeypatch.setattr(hd, "_run", lambda req, env, *a: calls.append(src(req, env)) or
                        ("error", "GatedRepoError: 401 Client Error", 0))
    calls.clear()
    with pytest.raises(RuntimeError, match="401"):
        hd.download("a/b")
    assert calls == ["http"]


def test_link_parse_and_bilibili_qr_login(tmp_path, monkeypatch):
    """Dòng chia sẻ của app -> link; đăng nhập Bilibili bằng mã QR -> file cookie cho yt-dlp."""
    import email.message
    import time

    from trami import sitelogin as sl

    assert sl.extract_url("【【大鸣王潮】岁共交流会-哔哩哔哩】 https://b23.tv/AbC123x") == "https://b23.tv/AbC123x"
    assert sl.extract_url("7.43 复制打开抖音，看看【作品】 https://v.douyin.com/iRNBho6u/ 复制此链接") == \
        "https://v.douyin.com/iRNBho6u/"
    assert sl.extract_url("xem cái này https://www.bilibili.com/video/BV1xx411c7mD?p=2，hay lắm") == \
        "https://www.bilibili.com/video/BV1xx411c7mD?p=2"
    assert sl.extract_url("BV1xx411c7mD") == "https://www.bilibili.com/video/BV1xx411c7mD"
    assert sl.extract_url("không có link") == ""
    assert sl.is_bilibili("https://b23.tv/x") and sl.is_bilibili("https://m.bilibili.com/video/BV1")
    assert not sl.is_bilibili("https://www.youtube.com/watch?v=1") and not sl.is_bilibili("https://notbilibili.com/")

    # link điện thoại / rút gọn / chia sẻ -> dạng yt-dlp có bộ tải riêng (không thì rơi vào bộ tải chung -> lỗi "html")
    short = {"https://b23.tv/AbC": "https://m.bilibili.com/video/BV1xx411c7mD?share_source=copy&p=2",
             "https://v.douyin.com/iRN/": "https://www.iesdouyin.com/share/video/7312345678901234567/?region=VN"}
    norm = {"https://m.bilibili.com/video/BV1xx411c7mD?spm=1": "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://b23.tv/AbC": "https://www.bilibili.com/video/BV1xx411c7mD?p=2",
            "https://www.bilibili.com/list/watchlater?bvid=BV1xx411c7mD&oid=1":
                "https://www.bilibili.com/video/BV1xx411c7mD",
            "https://m.bilibili.com/bangumi/play/ep123": "https://www.bilibili.com/bangumi/play/ep123",
            "https://www.bilibili.com/video/av170001?p=3": "https://www.bilibili.com/video/av170001?p=3",
            "https://v.douyin.com/iRN/": "https://www.douyin.com/video/7312345678901234567",
            "https://www.douyin.com/jingxuan?modal_id=7312345678901234567":
                "https://www.douyin.com/video/7312345678901234567",
            "https://www.youtube.com/watch?v=abc": "https://www.youtube.com/watch?v=abc"}
    for raw, want in norm.items():
        assert sl.normalize_url(raw, lambda u: short.get(u, u)) == want, raw
    try:
        from yt_dlp.extractor import gen_extractor_classes
    except ImportError:
        gen_extractor_classes = None
    if gen_extractor_classes:
        def ie(u):
            return next((e.IE_NAME for e in gen_extractor_classes() if e.suitable(u) and e.IE_NAME != "generic"),
                        "generic")
        assert ie("https://m.bilibili.com/video/BV1xx411c7mD") == "generic"      # nguyên nhân lỗi cũ
        assert {ie(v) for k, v in norm.items() if "youtube" not in k} <= {"BiliBili", "BiliBiliBangumi", "Douyin"}

    monkeypatch.setattr(sl, "BILI_FILE", tmp_path / "bili.txt")
    replies = iter([({"code": 0, "data": {"code": 86101}}, None), ({"code": 0, "data": {"code": 86090}}, None)])
    monkeypatch.setattr(sl, "_get", lambda url, *a, **k: next(replies))
    assert sl.bili_qr_poll("k")["state"] == "wait" and sl.bili_qr_poll("k")["state"] == "scanned"
    h = email.message.Message()
    h["Set-Cookie"] = "SESSDATA=ab%2C1790000000%2Cxy*c1; Path=/; Domain=bilibili.com; HttpOnly; Secure"
    h["Set-Cookie"] = "bili_jct=jct123; Path=/; Domain=bilibili.com"
    exp = int(time.time()) + 1000
    ok = {"code": 0, "data": {"code": 0, "url": f"https://passport.biligame.com/x?DedeUserID=42&Expires={exp}"
                                                f"&SESSDATA=other&gourl=x"}}
    monkeypatch.setattr(sl, "_get", lambda url, *a, **k: (ok, h))
    assert sl.bili_qr_poll("k")["state"] == "ok"
    ck = sl.read_cookies(tmp_path / "bili.txt")
    assert ck == {"SESSDATA": "ab%2C1790000000%2Cxy*c1", "bili_jct": "jct123", "DedeUserID": "42"}
    assert str(exp) in (tmp_path / "bili.txt").read_text(encoding="utf-8")
    assert sl.bili_cookie_file() == tmp_path / "bili.txt"
    monkeypatch.setattr(sl, "_get", lambda url, cookies=None, **k: (
        {"code": 0, "data": {"isLogin": cookies.get("SESSDATA") == ck["SESSDATA"], "uname": "Tra", "vipStatus": 1}},
        None))
    assert sl.bili_status() == {"login": True, "name": "Tra", "vip": True}
    monkeypatch.setattr(sl, "_get", lambda url, *a, **k: ({"code": 0, "data": {"code": 86038}}, None))
    assert sl.bili_qr_poll("k")["state"] == "expired"
    sl.bili_logout()
    assert sl.bili_cookie_file() is None and sl.bili_status() == {"login": False}
    m = sl.qr_matrix("https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key=abc")
    assert m.shape[0] == m.shape[1] >= 21 and m.any() and not m.all()


def test_download_video_share_text_and_cookie_fallback(tmp_path, monkeypatch):
    """Tải bằng yt-dlp thật từ máy chủ trên máy: dán cả dòng chia sẻ, tiến độ, cookie trình duyệt lỗi -> tải không cookie."""
    pytest.importorskip("yt_dlp")
    import functools
    import http.server
    import threading

    from trami import pipeline

    www = tmp_path / "www"
    www.mkdir()
    subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=160x120:r=10",
                    "-f", "lavfi", "-i", "sine=f=440", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(www / "clip.mp4")], check=True)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(www)))
    srv.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    url = f"http://127.0.0.1:{srv.server_address[1]}/clip.mp4"
    try:
        msgs, notes = [], []
        path = pipeline.download_video(f"【thử】 {url} 复制此链接", tmp_path / "a", lambda x, m: msgs.append((x, m)),
                                       notes=notes, settings={})
        assert path.exists() and path.suffix == ".mp4" and ff.probe(path).duration > 1.5
        assert msgs[-1][0] == 1.0 and all(x <= 1.0 for x, _ in msgs) and not notes
        notes = []
        path = pipeline.download_video(url, tmp_path / "b", None, notes=notes,
                                       settings={"dl_browser": "chrome"})   # máy thử không có Chrome
        assert path.exists() and notes and "cookie" in notes[0]
        with pytest.raises(ValueError, match="Không tìm thấy link"):
            pipeline.download_video("chỉ có chữ", tmp_path / "c", settings={})
    finally:
        srv.shutdown()
    assert "Cập nhật trình tải" in pipeline._dl_error(Exception("HTTP Error 412: Precondition Failed"), url, False)
    assert "Firefox" in pipeline._dl_error(Exception("Fresh cookies (not necessarily logged in) are needed"), url, False)
    assert "mã QR" in pipeline._dl_error(Exception("This video requires login"), "https://b23.tv/x", False)
    assert pipeline.ytdlp_version()


def test_link_login_gui(tmp_path, monkeypatch):
    """Trang chủ: trạng thái đăng nhập Bilibili, hộp mã QR (giả lập Bilibili), dự án trống bị xoá khi tải lỗi."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import sitelogin as sl
    from trami.gui import theme
    from trami.gui.link_login import BiliLoginDialog, qr_pixmap
    from trami.gui.main import MainWindow
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    monkeypatch.setattr(sl, "BILI_FILE", tmp_path / "bili.txt")
    state = {"login": False}
    monkeypatch.setattr(sl, "bili_status", lambda: {"login": True, "name": "Tra"} if state["login"] else {"login": False})
    win = MainWindow()
    win.show()

    def wait(cond, ms=3000):
        t = 0
        while not cond() and t < ms:
            QTest.qWait(20)
            t += 20
        return cond()

    home = win.home
    home._bili_refresh()
    assert wait(lambda: "480p" in home.bili_lbl.text()) and home.bili_btn.isVisibleTo(home)
    assert "yt-dlp" in home.ytdlp_lbl.text() or "Chưa cài" in home.ytdlp_lbl.text()

    pm = qr_pixmap("https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key=abc")
    assert pm.width() == pm.height() >= 200
    polls = iter([{"state": "wait"}, {"state": "scanned"}, {"state": "ok"}])
    monkeypatch.setattr(sl, "bili_qr_start", lambda: {"url": "https://x/scan?qrcode_key=k1", "key": "k1"})
    monkeypatch.setattr(sl, "bili_qr_poll", lambda key: next(polls))
    dlg = BiliLoginDialog(home)
    dlg.show()
    assert wait(lambda: dlg.key == "k1" and dlg.qr.pixmap() is not None and not dlg.qr.pixmap().isNull())
    dlg._poll()
    assert wait(lambda: "Chờ quét" in dlg.status.text() and not dlg._busy)
    dlg._poll()
    assert wait(lambda: "xác nhận" in dlg.status.text() and not dlg._busy)
    dlg._poll()
    assert wait(lambda: "Đã đăng nhập" in dlg.status.text())
    assert wait(lambda: dlg.result() == 1, 2000)
    state["login"] = True
    home._bili_refresh()
    assert wait(lambda: "Tra" in home.bili_lbl.text()) and home.bili_out.isVisibleTo(home)

    # tải từ link lỗi -> dự án trống "Video từ link" bị xoá, đang mở thì về Trang chủ
    p = Project.create("Video từ link")
    win.open_project(p.id)
    win._drop_empty_project(p.id)
    assert not p.dir.exists() and win.project_page.p is None
    q = Project.create("Có video")
    q.data["source"] = "x.mp4"
    q.save()
    win._drop_empty_project(q.id)
    assert q.dir.exists()
    win.close()


def test_one_speaker_and_auto_tab_follow_settings(sample_video, monkeypatch):
    """Cài đặt 'Số người nói = 1' -> gán hết S1, không đo giọng / không tách câu; công tắc tab Tự động = Cài đặt."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from trami import asr, asr_funasr, config, diarize as dz, pipeline
    from trami.project import Project

    monkeypatch.setattr(asr_funasr, "available", lambda: True)
    monkeypatch.setattr(asr_funasr, "transcribe_sensevoice",
                        lambda *a, **k: [Segment(0.5, 2.0, "你好。"), Segment(2.5, 4.0, "朋友。")])
    monkeypatch.setattr(asr, "transcribe", lambda *a, **k: [])
    monkeypatch.setattr(dz, "assign_speakers", lambda *a, **k: (_ for _ in ()).throw(AssertionError("không được đo giọng")))
    p = Project.create("one")
    pipeline.attach_source(p, sample_video)
    st = {"asr_engine": "sensevoice", "sep_before_asr": False, "diarize": True, "diarize_speakers": 1,
          "asr_max_chars": 22}
    pipeline.recognize(Project(p.id), "asr", "zh", settings=st)
    pr = Project(p.id)
    assert [s.speaker for s in pr.segments] == ["S1", "S1"]
    assert "1 người nói" in pipeline.speaker_note(pr, st)
    assert pipeline.diarize(pr, st) == 1   # nút Phân biệt người nói cũng vậy
    assert pipeline.speaker_note(pr, {"diarize": False}) == "phân biệt người nói: tắt"
    (pr.work / "stems").mkdir(parents=True, exist_ok=True)
    from trami.separate import ENSEMBLE

    (pr.work / "stems" / "model.txt").write_text(ENSEMBLE, "utf-8")
    assert pipeline.sep_model_name(pr) == "Kết hợp BS + Mel-RoFormer"

    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami.gui.main import MainWindow
    from trami.separate import BS_ROFORMER

    app = QApplication.instance() or QApplication([])
    config.save_settings({"diarize": True, "sep_before_asr": True})
    win = MainWindow()
    win.open_project(pr.id)
    pp = win.project_page
    assert pp.a_spk.isChecked() and pp.a_sep.isChecked()
    # đổi ở Cài đặt khi tool đang mở -> tab Tự động theo ngay (trước đây giữ giá trị lúc mở tool)
    config.save_settings({"diarize": False, "sep_before_asr": False, "sep_model": BS_ROFORMER})
    pp.settings = config.load_settings()
    pp.sync_engine()
    assert not pp.a_spk.isChecked() and not pp.a_sep.isChecked() and pp.d_sepmodel.currentData() == BS_ROFORMER
    assert config.load_settings()["diarize"] is False
    # bật ở tab Tự động -> Cài đặt đổi theo
    pp.a_spk.setChecked(True)
    pp.d_sepmodel.setCurrentIndex(pp.d_sepmodel.findData(ENSEMBLE))
    s2 = config.load_settings()
    assert s2["diarize"] is True and s2["sep_model"] == ENSEMBLE and s2["sep_before_asr"] is False
    win.close()


def test_ticks_follow_sentences_after_merge_delete(sample_video):
    """Tích câu rồi gộp / xoá câu: dấu tích đi theo đúng câu (trước đây giữ số thứ tự cũ -> IndexError khi Tạo lại giọng)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import pipeline
    from trami.gui.main import MainWindow
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    p = Project.create("ticks")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(i * 0.5, i * 0.5 + 0.4, f"句{i}", f"câu {i}") for i in range(6)]
    p.save()
    win = MainWindow()
    win.open_project(p.id)
    pp, m = win.project_page, win.project_page.model
    m.set_picked([4, 5])
    pp._sub_op("merge", [0, 1])                       # gộp 2 câu đầu -> các câu sau lùi lên 1
    assert [m.segs[r].trans for r in sorted(m.selected)] == ["câu 4", "câu 5"] and m.selected == {3, 4}
    pp._sub_op("delete", [4])                         # xoá câu đang tích cuối cùng
    assert m.selected == {3} and m.segs[3].trans == "câu 4"
    m.set_picked([1])
    pp._sub_op("merge", [0, 1])                       # gộp câu chưa tích với câu đã tích -> câu gộp vẫn tích
    assert m.selected == {0, 2} and m.segs[2].trans == "câu 4"
    calls = []
    pp.submit = lambda *a, **k: calls.append(a)       # không chạy lồng tiếng thật
    pp._redub_picked()                                # trước đây: IndexError
    pp.flush()                                        # lưu ngay (bình thường lưu sau 0,x giây)
    pp.reload()                                       # đọc lại dự án (vd. xong tác vụ): cùng số câu -> giữ tích
    assert m.selected == {0, 2}
    win.close()


def test_skills_cleanup_is_validated():
    """AI sửa lỗi nghe & nối câu: chỉ áp dụng chỗ sửa gần chữ cũ, chỉ gộp 2–4 dòng liền nhau, nghỉ ≤ 1,5 s."""
    from trami import skills

    segs = [Segment(0, 0.5, "真", "Thật"), Segment(0.6, 1.5, "的假的", "giả"), Segment(2, 3, "我可是最强"),
            Segment(3.1, 4, "AI。你先别走"), Segment(5, 6, "北京雄起"), Segment(10, 11, "远的"), Segment(20, 21, "好")]
    res = {"fix": {1: "真的假的", 3: "我可是最强AI。", 4: "你先别走", 5: "北极熊骑", 6: "今天天气很好很好很好"},
           "merge": [[1, 2], [6, 7], [2, 3, 4, 5, 6]], "unsure": {5: "đoán theo hình"}}
    new, stats = skills.apply_cleanup(segs, res)
    assert [s.text for s in new] == ["真的假的", "我可是最强AI。", "你先别走", "北极熊骑", "远的", "好"]
    assert (new[0].start, new[0].end, new[0].trans) == (0, 1.5, "Thật giả")
    assert new[0].extra["src_before"] == "真 | 的假的" and new[3].extra["src_unsure"] == "đoán theo hình"
    assert stats["merged"] == 1 and stats["fixed"] == 3 and stats["changed"] == [0, 1, 2, 3] and stats["unsure"] == [3]
    assert "src_before" not in new[4].extra       # câu khác hẳn -> bỏ qua; gộp 2 dòng cách 9 s / 5 dòng -> bỏ qua
    assert skills.skill_text({"translate_skill": "meme"}).startswith("- Video hài")
    own = {"translate_skills": [{"id": "u1", "name": "Của tôi", "text": "- 小克 -> Claude"}], "translate_skill": "u1"}
    assert skills.skill_text(own) == "- 小克 -> Claude" and len(skills.all_skills(own)) == len(skills.BUILTIN) + 2
    assert "小克 -> Claude" in skills.with_skill("SYS", "- 小克 -> Claude") and skills.with_skill("SYS", "") == "SYS"


def test_ai_skills_in_translate_pipeline(monkeypatch):
    """Dịch cả phim bằng AI (bất kỳ API nào): sửa lỗi nghe & nối câu -> hồ sơ phim -> dịch (kèm kỹ năng) -> kiểm tra."""
    from trami.translate import parse_glossary
    from trami import pipeline
    from trami import translate as tr
    from trami.project import Project

    calls = []

    def fake_chat(self, system, user, max_tokens=8000, temperature=0.3):
        kind = ("clean" if "biên tập viên phụ đề tiếng Trung" in system else "review" if "kiểm tra bản dịch" in system
                else "profile" if 'lập "HỒ SƠ PHIM"' in system else "translate")
        calls.append((kind, system, user))
        if kind == "clean":
            return json.dumps({"fix": {"1": "真的假的", "3": "D神你好"}, "merge": [[1, 2]], "unsure": {}})
        if kind == "profile":
            return json.dumps({"the_loai": "hoạt hình hài về AI", "nhan_vat": ["D神 -> Thần D – cá voi"],
                               "xung_ho": ["các AI: tớ – cậu"], "thuat_ngu": {"D神": "Thần D"}}, ensure_ascii=False)
        payload = json.loads(user[user.index("{"):])
        if kind == "review":
            first = min(payload, key=int)
            return json.dumps({first: {"sua": "Thật hay đùa vậy?", "ly_do": "tự nhiên hơn"}}, ensure_ascii=False)
        return json.dumps({k: f"Câu {k}" for k in payload}, ensure_ascii=False)

    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    st_ = {"translate_engine": "openai", "openai_api_key": "x", "openai_base_url": "https://api.example.com/v1",
           "dub_fit": False, "translate_skill": "meme", "ai_clean_before_translate": True,
           "ai_review_after_translate": True}
    p = Project.create("ky_nang")
    p.segments = [Segment(0, 0.5, "真"), Segment(0.6, 1.5, "的假的"), Segment(2, 3, "地神你好")]
    p.save()
    pipeline.translate(p, "openai", "auto", False, st_)
    kinds = [k for k, _, _ in calls]
    assert kinds[0] == "clean" and kinds[1] == "profile" and "translate" in kinds and kinds[-1] == "review"
    assert all("Ủa alo" in sysp for _, sysp, _ in calls)                    # kỹ năng gắn vào mọi lời nhắc
    assert any("Thần D" in u for k, _, u in calls if k == "translate")     # hồ sơ phim đi kèm khi dịch
    segs = p.segments
    assert [s.text for s in segs] == ["真的假的", "D神你好"] and segs[0].extra["src_before"] == "真 | 的假的"
    assert segs[0].trans == "Thật hay đùa vậy?" and segs[0].extra["review_note"] == "tự nhiên hơn"
    assert segs[0].extra["trans_before_review"] == "Câu 1" and segs[1].trans == "Câu 2"
    assert "Thần D" in p.data["film_profile"] and parse_glossary(p.data["glossary"]) == {"D神": "Thần D"}
    assert "nối 1" in p.data["skill_note"] and "kiểm tra và sửa 1" in p.data["skill_note"]
    # dịch lại cả phim: không sửa lỗi nghe lần 2, không lập lại hồ sơ phim (dùng lại bản đã lưu / đã sửa)
    calls.clear()
    pipeline.translate(p, "openai", "auto", False, st_)
    assert "clean" not in [k for k, _, _ in calls] and "profile" not in [k for k, _, _ in calls]
    # câu sửa tay: AI kiểm tra không đụng tới
    segs = p.segments
    segs[0].trans, segs[0].extra["trans_user"] = "Tớ tự sửa", True
    p.segments = segs
    p.save()
    assert pipeline.ai_review(p, st_, "openai") == [1] and p.segments[0].trans == "Tớ tự sửa"
    # hoàn tác sửa lỗi nghe -> về 3 câu gốc
    assert pipeline.undo_cleanup(p, st_) == 3 and [s.text for s in p.segments] == ["真", "的假的", "地神你好"]
    # Google / Hy-MT2: không phải AI hội thoại -> không có kỹ năng, báo rõ khi bấm nút
    with pytest.raises(ValueError, match="cần công cụ dịch AI"):
        pipeline.ai_cleanup(p, st_, "google")
    calls.clear()
    monkeypatch.setattr(tr.Translator, "translate_lines", lambda self, lines, *a, **k: ["x"] * len(lines))
    pipeline.translate(p, "google", "auto", False, dict(st_, translate_engine="google"))
    assert not calls


def test_skills_gui(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import config
    from trami.gui.main import MainWindow
    from trami.gui.skills_dialog import SkillsDialog
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    config.save_settings({"translate_engine": "deepseek", "translate_skills": [], "translate_skill": ""})
    dlg = SkillsDialog(None, "meme")
    assert dlg.text.isReadOnly() and not dlg.b_save.isEnabled()      # kỹ năng có sẵn: chỉ đọc
    dlg._copy()                                                       # chép thành kỹ năng của tôi -> sửa được
    assert not dlg.text.isReadOnly()
    dlg.name.setText("Phim AI của tôi")
    dlg.text.setPlainText("- 小克 -> Claude")
    dlg._save()
    dlg._use()
    st_ = config.load_settings()
    own = st_["translate_skills"]
    assert len(own) == 1 and own[0]["name"] == "Phim AI của tôi" and st_["translate_skill"] == own[0]["id"]
    dlg.deleteLater()

    p = Project.create("ky_nang_gui")
    p.segments = [Segment(0, 1, "真"), Segment(1.1, 2, "的假的")]
    p.data["film_profile"] = "Thể loại: hài"
    p.save()
    win = MainWindow()
    win.open_project(p.id)
    pp = win.project_page
    assert pp.a_skill.currentData() == own[0]["id"] and pp.profile_edit.toPlainText() == "Thể loại: hài"
    pp.a_review.setChecked(True)
    assert config.load_settings()["ai_review_after_translate"] is True
    pp.profile_edit.setPlainText("Thể loại: hài meme")
    pp._save_edit_now()
    assert Project(p.id).data["film_profile"] == "Thể loại: hài meme"
    jobs = []
    pp.submit = lambda *a, **k: jobs.append(a[0])
    pp.run_ai_clean()
    pp.run_profile()
    assert jobs == ["clean", "profile"]
    toasts = []
    monkeypatch.setattr(win, "toast", lambda msg, kind="info", *a, **k: toasts.append(msg))
    from trami.gui.widgets import set_combo
    set_combo(pp.a_engine, "google")
    pp.run_ai_review()
    assert jobs == ["clean", "profile"] and "cần công cụ dịch AI" in toasts[-1]
    config.save_settings({"translate_skill": "", "translate_skills": [], "ai_review_after_translate": False,
                          "translate_engine": "google"})
    win.close()


def test_subs_tab_ticks_and_translate_options(monkeypatch):
    """Tab Phụ đề: cột Chọn (tích) thay 3 cột lồng tiếng, ô cách dịch dùng chung với tab Tự động, Gộp / Dịch theo câu
    đã tích."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import config
    from trami.gui.main import MainWindow
    from trami.gui.subs_model import C_FIT, C_NODUB, C_SEL, C_SONG
    from trami.gui.widgets import set_combo
    from trami.project import Project

    QApplication.instance() or QApplication([])
    config.save_settings({"translate_engine": "google", "translate_style": "auto"})
    p = Project.create("tich_phu_de")
    p.segments = [Segment(0, 1, "当当，一只真"), Segment(2.5, 4, "的鹈鹕。"), Segment(5, 6, "你好"), Segment(7, 8, "再见")]
    p.save()
    win = MainWindow()
    win.open_project(p.id)
    pp, m = win.project_page, win.project_page.model
    t = pp.table
    assert not t.isColumnHidden(C_SEL) and t.horizontalHeader().visualIndex(C_SEL) == 0
    assert all(t.isColumnHidden(c) for c in (C_SONG, C_NODUB, C_FIT))
    assert pp.dub_table.isColumnHidden(C_SONG) and pp.dub_table.isColumnHidden(C_NODUB)   # 2.0.59: đổi bằng nút
    # ô cách dịch ở tab Phụ đề <-> tab Tự động <-> Cài đặt
    set_combo(pp.s_engine, "deepseek")
    assert pp.a_engine.currentData() == "deepseek" and config.load_settings()["translate_engine"] == "deepseek"
    set_combo(pp.a_style, "hai_huoc") if pp.a_style.findData("hai_huoc") >= 0 else None
    assert pp.s_style.currentData() == pp.a_style.currentData()
    assert pp.s_skill.count() == pp.a_skill.count()
    # bấm tiêu đề "Chọn" -> tích hết / bỏ hết
    pp._toggle_pick_all(pp.proxy)
    assert m.selected == {0, 1, 2, 3} and "đã tích 4" in pp.seg_count.text()
    pp._toggle_pick_all(pp.proxy)
    assert m.selected == set()
    # tích 2 câu bị cắt -> Gộp -> câu gộp vẫn tích -> Dịch câu đã tích dịch đúng câu đó
    m.set_picked([0, 1])
    pp._seg_op("merge")
    assert [s.text for s in m.segs][:2] == ["当当，一只真的鹈鹕。", "你好"] and m.selected == {0}
    calls = []
    monkeypatch.setattr(pp, "_translate_rows", lambda rows, only_empty: calls.append((rows, only_empty)))
    pp._translate_picked()
    assert calls == [([0], False)]
    m.set_picked([0, 2])
    toasts = []
    monkeypatch.setattr(win, "toast", lambda msg, kind="info", *a, **k: toasts.append(msg))
    pp._seg_op("merge")                                                 # không liền nhau -> không gộp
    assert len(m.segs) == 3 and "liền nhau" in toasts[-1]
    config.save_settings({"translate_engine": "google"})
    win.close()


def test_split_rules_words_latin_tail_and_styles():
    """Tách câu 2.0.57: "AI。" thuộc câu trước; chỗ nghỉ không dấu câu không cắt giữa từ / sau từ treo / trước trợ từ;
    mẩu cụt cuối câu gộp lại + kéo mốc; 2 kiểu tách câu (trọn ý / câu ngắn)."""
    pytest.importorskip("jieba")
    from trami import align

    dub, short = align.split_params({"split_style": "dub"}), align.split_params({"split_style": "short"})
    assert align.split_params({}) == dub and dub["chunk_gap"] == 1.5 and short["chunk_gap"] == 1.0

    def run(text, gaps=None, q=short, per=0.2, exact=True):
        pos = align._char_positions(text)
        t, times = 0.0, []
        for k in range(len(pos)):
            times.append((t, t + per))
            t += per + ((gaps or {}).get(k, 0.0))
        seg = Segment(0, t, text)
        return [(x.text, x.start, x.end) for x in align.split_timed(seg, times, exact, 28, 0.0, q)]

    texts = lambda out: [x[0] for x in out]  # noqa: E731
    # chữ Latin trước dấu kết câu đi theo câu trước, mốc cuối câu nới thêm cho "AI"
    out = run("我可是最强AI。你先别最强了", gaps={4: 1.0})
    assert texts(out) == ["我可是最强AI。", "你先别最强了"] and out[0][2] > 5 * 0.2 + 0.3
    assert align._split_text("爸最好了，6块钱", align._char_positions("爸最好了，6块钱"), [3]) == ["爸最好了，", "6块钱"]
    # nghỉ dài giữa 1 từ (真|的, 好|吃), sau từ treo (一只|), trước trợ từ (|呢) -> không cắt
    assert texts(run("当当一只真的鹈鹕", gaps={4: 1.6})) == ["当当一只真的鹈鹕"]
    assert texts(run("放开我不好吃", gaps={4: 2.0})) == ["放开我不好吃"]
    assert texts(run("我买了一只鹈鹕回来", gaps={4: 1.2})) == ["我买了一只鹈鹕回来"]
    assert texts(run("你到底在说什么呢", gaps={6: 1.2})) == ["你到底在说什么呢"]
    # ranh giới từ thật vẫn cắt được (kiểu câu ngắn)
    assert texts(run("快想办法给我变回去估计额度用完了", gaps={8: 0.9})) == ["快想办法给我变回去", "估计额度用完了"]
    # mẩu cụt cuối câu ("日子。" sau chỗ nghỉ 1,2 s) gộp về câu trước, mốc kéo về sát chữ trước
    out = run("让他看看我过的什么日子。", gaps={8: 1.2})
    assert texts(out) == ["让他看看我过的什么日子。"] and out[0][2] < 11 * 0.2 + 0.2
    # kiểu câu trọn ý: phẩy + nghỉ 0,5 s / nghỉ 1 s không dấu câu -> không cắt; nghỉ 1,8 s -> cắt
    assert texts(run("我们先走吧，他们马上就到", gaps={4: 0.5})) == ["我们先走吧，", "他们马上就到"]
    assert texts(run("我们先走吧，他们马上就到", gaps={4: 0.5}, q=dub)) == ["我们先走吧，他们马上就到"]
    assert texts(run("快想办法给我变回去估计额度用完了", gaps={8: 1.0}, q=dub)) == ["快想办法给我变回去估计额度用完了"]
    assert len(run("快想办法给我变回去估计额度用完了", gaps={8: 1.8}, q=dub)) == 2


def test_join_broken_lines():
    """Nối câu bị cắt ngang (không cần AI) trên đúng các dòng người dùng gửi; giữ nguyên câu không đổi."""
    pytest.importorskip("jieba")
    from trami import align

    def lines():
        return [Segment(53.28, 54.98, "那是当然，", "Đương nhiên rồi,"), Segment(55.36, 56.42, "我可是最强"),
                Segment(57.44, 61.22, "AI。你先别最强了，已经有十份鹈鹕自行车等着你生成。"),
                Segment(61.28, 63.78, "不是哥们，不是说好的鹈鹕测试吗？", "Ủa alo"),
                Segment(101.58, 101.98, "不，"), Segment(102.86, 103.52, "我不困了。"),
                Segment(121.13, 122.75, "恭喜业务翻倍了，"), Segment(123.13, 128.59, "真的不想写了，我现在满脑子都是鹈鹕啊！"),
                Segment(130.49, 131.71, "当当，一只真"), Segment(133.29, 136.03, "的鹈鹕，这个必须发我爸，让他看看我过的什么日"),
                Segment(137.21, 137.61, "子。"), Segment(138.01, 139.31, "你从哪儿弄来的的？"),
                Segment(151.38, 151.78, "放开我，不好"), Segment(154.21, 154.61, "吃。"), Segment(157.37, 157.87, "嘎！")]

    src = lines()
    out, made = align.join_broken(src, align.SPLIT_STYLES["dub"])
    assert [s.text for s in out] == [
        "那是当然，我可是最强AI。", "你先别最强了，已经有十份鹈鹕自行车等着你生成。", "不是哥们，不是说好的鹈鹕测试吗？",
        "不，我不困了。", "恭喜业务翻倍了，", "真的不想写了，我现在满脑子都是鹈鹕啊！",
        "当当，一只真的鹈鹕，这个必须发我爸，让他看看我过的什么日子。", "你从哪儿弄来的的？", "放开我，不好吃。", "嘎！"]
    assert made == 5 and out[2] is src[3] and out[2].trans == "Ủa alo"          # câu không đổi: giữ nguyên
    assert out[6].end < 137.0 and out[8].end < 153.0                              # mẩu cụt "子。" / "吃。" kéo mốc về
    assert all(a.end <= b.start for a, b in zip(out, out[1:]))
    # kiểu câu ngắn: chỉ nối chỗ chắc chắn bị cắt ngang
    out, _ = align.join_broken(lines(), align.SPLIT_STYLES["short"])
    assert "那是当然，" in [s.text for s in out] and "我可是最强AI。" in [s.text for s in out]
    assert "放开我，不好吃。" in [s.text for s in out] and "不，" in [s.text for s in out]
    # khác người nói / đoạn hát -> không nối
    a, b = Segment(0, 1, "当当，一只真", speaker="S1"), Segment(1.5, 3, "的鹈鹕。", speaker="S2")
    assert align.join_broken([a, b])[1] == 0
    a, b = Segment(0, 1, "当当，一只真"), Segment(1.5, 3, "的鹈鹕。")
    b.extra["song"] = True
    assert align.join_broken([a, b])[1] == 0
    # phụ đề không dấu câu (OCR): chỉ nối khi cắt giữa từ
    ocr = [Segment(0, 1, "你好"), Segment(1.3, 2, "再见"), Segment(3, 4, "我们走"), Segment(4.5, 5, "一只真"),
           Segment(5.4, 6, "的鹈鹕")]
    out, made = align.join_broken(ocr)
    assert [s.text for s in out] == ["你好", "再见", "我们走", "一只真的鹈鹕"] and made == 1


def test_join_lines_button_and_undo(monkeypatch):
    pytest.importorskip("jieba")
    from trami import pipeline, skills
    from trami.project import Project

    p = Project.create("noi_cau")
    p.segments = [Segment(10.49, 11.71, "当当，一只真", "Tèn ten, một con"),
                  Segment(13.29, 16.03, "的鹈鹕，这个必须发我爸，让他看看我过的什么日", "bồ nông thật…"),
                  Segment(17.21, 17.61, "子。", "ngày gì đây."), Segment(18.01, 19.31, "你从哪儿弄来的的？", "Cậu kiếm ở đâu?")]
    p.save()
    rows = pipeline.join_lines(p, {"split_style": "dub"})
    segs = p.segments
    assert rows == [0] and len(segs) == 2 and segs[0].text.endswith("什么日子。")
    assert segs[0].trans == "Tèn ten, một con bồ nông thật… ngày gì đây." and segs[1].trans == "Cậu kiếm ở đâu?"
    assert pipeline.join_lines(p, {"split_style": "dub"}) == []                 # chạy lại: không còn gì để nối
    assert pipeline.undo_join(p) == 4 and len(p.segments) == 4 and pipeline.undo_join(p) == 0
    # AI sửa lỗi nghe & nối câu: giờ nhận 2 dòng cách 1,58 s ("一只真" | "的鹈鹕")
    new, st = skills.apply_cleanup(p.segments, {"merge": [[1, 2]], "fix": {}, "unsure": {}})
    assert st["merged"] == 1 and new[0].text.startswith("当当，一只真的鹈鹕")


def test_join_button_and_split_style_gui(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("jieba")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import config
    from trami.gui.main import MainWindow
    from trami.gui.widgets import set_combo
    from trami.project import Project

    QApplication.instance() or QApplication([])
    config.save_settings({"split_style": "dub"})
    p = Project.create("noi_cau_gui")
    p.segments = [Segment(0, 1, "放开我，不好"), Segment(3.4, 3.8, "吃。"), Segment(5, 6, "嘎！")]
    p.save()
    win = MainWindow()
    win.open_project(p.id)
    pp = win.project_page
    assert pp.a_split.currentData() == "dub"
    set_combo(pp.a_split, "short")                                    # 1 giá trị chung với Cài đặt
    assert config.load_settings()["split_style"] == "short"
    win.settings_page.load()
    assert win.settings_page.split_style.currentData() == "short"     # trang Cài đặt cũng thấy
    jobs = []
    pp.submit = lambda kind, title, fn, **k: jobs.append((kind, fn(type("J", (), {"update": lambda *a: None})(), p)))
    pp.run_join()
    assert jobs[0][0] == "join" and jobs[0][1]["pick"] == [0]
    assert [s.text for s in p.segments] == ["放开我，不好吃。", "嘎！"]
    pp.reload()
    pp.run_undo_join()
    assert [s.text for s in pp.model.segs] == ["放开我，不好", "吃。", "嘎！"]
    config.save_settings({"split_style": "dub"})
    win.close()


def test_apply_translation_different_line_count():
    """Nạp SRT đã dịch khác số câu: file dịch gộp câu -> gộp câu gốc (không còn câu trống); tách câu -> nối bản dịch;
    câu chỉ có trong file dịch -> thêm câu mới."""
    from trami import pipeline

    segs = [Segment(i * 2, i * 2 + 1.5, f"句{i}", f"cũ {i}") for i in range(10)]
    segs[3].extra["tts_file"] = "x.wav"
    cues = [Segment(i * 2, i * 2 + 1.5, f"Câu {i}") for i in range(10) if i not in (3, 4)]
    cues.insert(3, Segment(6.0, 9.5, "Câu 3 và 4 gộp"))
    st = {}
    changed = pipeline.apply_translation(segs, cues, st)
    assert len(segs) == 9 and st["merged"] == 1 and all(s.trans for s in segs)
    assert (segs[3].text, segs[3].start, segs[3].end, segs[3].trans) == ("句3句4", 6, 9.5, "Câu 3 và 4 gộp")
    assert "tts_file" not in segs[3].extra and 3 in changed
    segs = [Segment(i * 2, i * 2 + 1.5, f"句{i}", "") for i in range(10)]
    cues = [Segment(i * 2, i * 2 + 1.5, f"Câu {i}") for i in range(10) if i != 2]
    cues += [Segment(4.0, 4.7, "Câu 2a"), Segment(4.8, 5.5, "câu 2b"), Segment(25, 26, "Thêm mới")]
    st = {}
    pipeline.apply_translation(segs, cues, st)
    assert len(segs) == 11 and st == {"merged": 0, "joined": 1, "added": 1, "songs": 0}
    assert segs[2].trans == "Câu 2a câu 2b" and (segs[-1].text, segs[-1].trans) == ("", "Thêm mới")
    # câu dịch chỉ chạm nhẹ câu bên cạnh (< 30 %) -> không gộp nhầm
    segs = [Segment(0, 2, "你好", ""), Segment(2.1, 4, "再见", "")]
    pipeline.apply_translation(segs, [Segment(0, 2.3, "Xin chào"), Segment(2.3, 4, "Tạm biệt"), Segment(3.9, 4.5, "x")])
    assert [s.trans for s in segs] == ["Xin chào", "Tạm biệt x"]


def test_join_keeps_or_translates_new_lines(monkeypatch):
    """Nối câu ở dự án đã dịch: câu mới = các dòng cũ ghép lại -> giữ bản dịch; câu tách lại -> tự dịch luôn (trước để
    trống)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    pytest.importorskip("jieba")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import align, config, translate
    from trami.gui.main import MainWindow
    from trami.project import Project

    out, _ = align.join_broken([Segment(0, 1, "放开我，不好", "Buông tôi ra, không"),
                                Segment(1.5, 2, "吃。", "ngon."), Segment(3, 4, "嘎！", "Quác!")])
    assert out[0].text == "放开我，不好吃。" and out[0].trans == "Buông tôi ra, không ngon."
    QApplication.instance() or QApplication([])
    config.save_settings({"split_style": "dub", "translate_engine": "google"})
    monkeypatch.setattr(translate.Translator, "translate_lines",
                        lambda self, lines, *a, **k: [f"VI {x}" for x in lines])
    p = Project.create("noi_cau_dich")
    p.segments = [Segment(53.28, 54.98, "那是当然，", "Đương nhiên rồi,"), Segment(55.36, 56.42, "我可是最强", "tôi là"),
                  Segment(57.44, 61.22, "AI。你先别最强了，已经有十份鹈鹕自行车等着你生成。", "AI mạnh nhất. Thôi bớt đi…")]
    p.save()
    win = MainWindow()
    win.open_project(p.id)
    pp = win.project_page
    jobs = []
    pp.submit = lambda kind, title, fn, **k: jobs.append(fn(type("J", (), {"update": lambda *a: None,
                                                                    "cancelled": lambda *a: False})(), p))
    pp.run_join()
    segs = p.segments
    assert [s.text for s in segs] == ["那是当然，我可是最强AI。", "你先别最强了，已经有十份鹈鹕自行车等着你生成。"]
    assert all(s.trans.startswith("VI ") for s in segs) and "đã dịch 2 câu mới" in jobs[0]["message"]
    win.close()



# ------------------------------------------------------------------ 2.0.59: mất mạng / lag, câu cảm thán, bài hát

def test_net_error_kinds_wait_and_cancel(monkeypatch):
    """Nhận ra lỗi mạng; mất mạng -> chờ có mạng rồi trả True; mạng vẫn có -> False; quá lâu -> NetworkDown; Huỷ ->
    dừng ngay (cả khi đang chờ mạng trả lời)."""
    import socket
    import time as _t

    import requests

    from trami import net

    assert net.is_net_error(requests.ConnectionError("Max retries exceeded … getaddrinfo failed"))
    assert net.is_net_error(requests.ReadTimeout("Read timed out"))
    assert net.is_net_error(socket.gaierror(11001, "getaddrinfo failed"))
    assert net.is_net_error(ConnectionResetError(10054, "forcibly closed"))
    assert not net.is_net_error(ConnectionRefusedError(10061, "refused"))           # Ollama chưa bật: không phải mạng
    assert not net.is_net_error(requests.ConnectionError("HTTPConnectionPool(host='localhost', port=11434)"))
    assert not net.is_net_error(ValueError("API key sai"))
    assert not net.is_net_error(ff.Cancelled())
    monkeypatch.setattr(net, "CHECK_EVERY", 0.05)
    seq = iter([False, False, False, True])
    monkeypatch.setattr(net, "online", lambda hosts=(), timeout=3.0: next(seq))
    msgs = []
    assert net.wait_online("dịch", report=msgs.append) is True
    assert any("Mất mạng" in m for m in msgs) and "có mạng lại" in msgs[-1]
    monkeypatch.setattr(net, "online", lambda hosts=(), timeout=3.0: True)
    assert net.wait_online("dịch") is False                                         # mạng vẫn có: máy chủ lỗi
    monkeypatch.setattr(net, "online", lambda hosts=(), timeout=3.0: False)
    with pytest.raises(net.NetworkDown):
        net.wait_online("dịch", max_wait=0.2, report=lambda m: None)
    flag = {"c": False}
    with pytest.raises(ff.Cancelled):
        with net.task(lambda: flag["c"], None):
            import threading

            threading.Timer(0.2, lambda: flag.update(c=True)).start()
            net.wait_online("dịch", max_wait=30)
    t0 = _t.time()
    with pytest.raises(ff.Cancelled):
        net.call(lambda: _t.sleep(5), cancel=lambda: _t.time() - t0 > 0.3)        # mạng đứng: Huỷ không phải đợi
    assert _t.time() - t0 < 2


def test_llm_waits_for_network_instead_of_failing(monkeypatch):
    """Rớt mạng giữa lúc dịch AI: chờ có mạng rồi gửi lại lô đó (không tính là lần hỏng, không báo lỗi)."""
    import requests

    from trami import net, translate

    monkeypatch.setattr(net, "CHECK_EVERY", 0.01)
    state = {"n": 0, "online": iter([False, True, False, True, False, True])}
    monkeypatch.setattr(net, "online", lambda hosts=(), timeout=3.0: next(state["online"]))

    class R:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": '{"1": "Xin chào"}'}}]}

    class Sess:
        def post(self, *a, **k):
            state["n"] += 1
            if state["n"] <= 3:
                raise requests.ConnectionError("Max retries exceeded: getaddrinfo failed")
            return R()

    monkeypatch.setattr(net, "session", lambda: Sess())
    monkeypatch.setattr(net, "sleep", lambda s, c=None: None)
    c = translate.LLMClient({"deepseek_api_key": "x"}, "deepseek")
    assert c.chat("sys", "user") == '{"1": "Xin chào"}' and state["n"] == 4
    # mạng vẫn có mà máy chủ không trả lời -> 4 lần rồi báo lỗi rõ
    state["n"] = -100
    monkeypatch.setattr(net, "online", lambda hosts=(), timeout=3.0: True)
    with pytest.raises(translate.ApiError, match="chập chờn"):
        c.chat("sys", "user")


def test_edge_tts_network_drop_no_truncated_files(monkeypatch, tmp_path):
    """Edge: rớt mạng giữa câu -> chờ có mạng rồi đọc lại; file dở không bị coi là đã đọc xong; Huỷ dừng ngay."""
    import asyncio
    import time as _t
    import types

    from trami import net, tts

    monkeypatch.setattr(net, "CHECK_EVERY", 0.01)
    on = {"v": False, "fails": 0, "checks": 0}

    def online(hosts=(), timeout=3.0):
        on["checks"] += 1
        if on["checks"] >= 3 and on["fails"] < 9:
            on["v"] = True                                       # kiểm tra lần 3: có mạng lại
        return on["v"]
    monkeypatch.setattr(net, "online", online)

    class Comm:
        def __init__(self, text, voice, **k):
            self.text = text

        async def save(self, path):
            Path(path).write_bytes(b"ID3partial")          # ghi được 1 phần rồi rớt mạng
            if not on["v"]:
                on["fails"] += 1
                raise ConnectionResetError(10054, "Cannot connect to host speech.platform.bing.com")
            if self.text == "treo":
                await asyncio.sleep(30)
            Path(path).write_bytes(b"ID3" + self.text.encode() * 50)

    monkeypatch.setitem(sys.modules, "edge_tts", types.SimpleNamespace(Communicate=Comm))
    items = [(i, f"câu {i}", tmp_path / f"t{i}.mp3", "vi-VN-HoaiMyNeural") for i in range(5)]
    msgs = []
    errs = asyncio.run(tts._edge_all(items, "+0%", "+0Hz", lambda x, m: msgs.append(m), None, 5, 0))
    assert errs == [] and all((tmp_path / f"t{i}.mp3").read_bytes().startswith(b"ID3c") for i in range(5))
    assert not list(tmp_path.glob("*.part.mp3")) and any("Mất mạng" in m for m in msgs)
    # mất mạng quá lâu: câu chưa đọc không để lại file cụt, báo NetworkDown
    on.update(v=False, fails=99)                                 # không bao giờ có mạng lại
    monkeypatch.setattr(net, "NET_WAIT", 0.1)
    monkeypatch.setattr(net, "NET_WAIT_QUICK", 0.1)
    items2 = [(0, "mới", tmp_path / "n0.mp3", "v")]
    with pytest.raises(net.NetworkDown):
        asyncio.run(tts._edge_all(items2, "+0%", "+0Hz", None, None, 1, 0))
    assert not (tmp_path / "n0.mp3").exists()
    # bấm Huỷ giữa câu đang chờ mạng trả lời
    on.update(v=True)
    t0 = _t.time()
    with pytest.raises(ff.Cancelled):
        asyncio.run(tts._edge_all([(0, "treo", tmp_path / "h.mp3", "v")], "+0%", "+0Hz", None,
                                  lambda: _t.time() - t0 > 0.3, 1, 0))
    assert _t.time() - t0 < 3


def test_worker_read_cancel_and_stall(tmp_path):
    """Tiến trình đọc giọng bị treo: Huỷ dừng được (tắt tiến trình sau 3 s), quá giờ -> WorkerStuck rồi mở lại."""
    import time as _t

    from trami import tts

    script = tmp_path / "w.py"
    script.write_text("import sys, time, json\n"
                      "print('@@TRAMI@@' + json.dumps({'ready': True, 'sr': 24000}), flush=True)\n"
                      "for line in sys.stdin:\n"
                      "    req = json.loads(line)\n"
                      "    if req['text'] == 'treo':\n"
                      "        time.sleep(60)\n"
                      "    print('@@TRAMI@@' + json.dumps({'ok': True}), flush=True)\n", encoding="utf-8")

    class W(tts.VieneuWorker):
        title = "Thử"
        stall_call = 1.0

        def _cmd(self):
            return [sys.executable, str(script)]

    w = W("x")
    w.ensure_started()
    assert w.sr == 24000
    assert w.synth("được", tmp_path / "a.wav") == tmp_path / "a.wav"
    with pytest.raises(RuntimeError, match="không phản hồi"):     # treo -> mở lại 1 lần -> vẫn treo -> báo lỗi
        w.synth("treo", tmp_path / "b.wav")
    assert w.synth("được", tmp_path / "c.wav")                   # tiến trình mới chạy tiếp được
    w.stall_call = 60
    t0 = _t.time()
    from trami import net

    with net.task(lambda: _t.time() - t0 > 0.3, None):
        with pytest.raises(ff.Cancelled):
            w.synth("treo", tmp_path / "d.wav")
    assert _t.time() - t0 < 6 and w.proc is None
    w.stop()


def test_lone_interjections_dropped_before_dub():
    """Câu CHỈ có 1–2 tiếng cảm thán bị xoá trước khi lồng tiếng Việt; câu có lời thật giữ nguyên; khôi phục được."""
    from trami import pipeline
    from trami.project import Project
    from trami.voicefix import lone_interjection

    for t in ("A!", "Ừm.", "Ái!", "Hả?", "Ha ha!", "Ồ…", "Ối!", "Hừ!", '"Ừm"'):
        assert lone_interjection(t), t
    for t in ("Ừm, đúng là như vậy.", "A Lan", "Ai?", "Vâng.", "Đi!", "Ừm ừm ừm", "Ơi!", "Ồ, 3 cái"):
        assert not lone_interjection(t), t
    p = Project.create("cam_than")
    p.segments = [Segment(0, 1, "啊", "A!"), Segment(1, 3, "嗯，是这样", "Ừm, đúng là như vậy."),
                  Segment(3, 4, "嗯", "Ừm."), Segment(5, 7, "走吧", "Đi thôi.")]
    p.save()
    assert pipeline.drop_interjections(p) == 2
    assert [s.trans for s in p.segments] == ["Ừm, đúng là như vậy.", "Đi thôi."]
    assert pipeline.restore_interjections(p) == 2
    segs = p.segments
    assert [s.trans for s in segs] == ["A!", "Ừm, đúng là như vậy.", "Ừm.", "Đi thôi."]
    assert pipeline.drop_interjections(p) == 0                   # đã khôi phục: không xoá lại
    # câu chưa có giọng trong bản lồng
    segs[1].extra["dub_at"] = [1, 2]
    segs[3].extra["song"] = True
    assert pipeline.undubbed_rows(segs) == [0, 2]
    assert pipeline.undubbed_rows(segs, skip_songs=False) == [0, 2, 3]


def test_song_marks_from_translation_and_pitch():
    """♪ trong bản dịch (file SRT dịch ngoài / AI) -> 🎵; giọng HÁT (nốt ngân) thành cụm -> 🎵, lời nói không."""
    from trami import pipeline, songs

    segs = [Segment(0, 2, "我要看你", ""), Segment(2, 4, "你好", "")]
    cues = [Segment(0, 2, "♪ Anh muốn nhìn em ♪"), Segment(2, 4, "Xin chào")]   # SRT dịch: lời ở .text
    stats: dict = {}
    pipeline.apply_translation(segs, cues, stats)
    assert segs[0].extra.get("song") and not segs[1].extra.get("song") and stats["songs"] == 1
    segs[0].extra.pop("song")
    segs[0].extra["not_song"] = True                             # người dùng đã bỏ 🎵: không tự đánh dấu lại
    assert pipeline.mark_song_lines(segs) == 0
    sr = 16000
    rng = np.random.default_rng(0)

    def tone(f0s, durs, vib=0.0):
        out = []
        for f, d in zip(f0s, durs):
            t = np.arange(int(d * sr)) / sr
            ph = 2 * np.pi * np.cumsum(f * (1 + vib * np.sin(2 * np.pi * 5.5 * t)) if np.ndim(f) == 0 else f) / sr
            out.append(sum(np.sin(k * ph) / k for k in range(1, 6)) * 0.3)
        return np.concatenate(out)

    def sung(n=8):        # nốt ngân 0,5 s có rung giọng, cách nhau 0,1 s
        notes = rng.choice([220, 247, 262, 294, 330], n)
        return np.concatenate([np.concatenate([tone([f], [0.5], 0.02), np.zeros(int(0.1 * sr))]) for f in notes])

    def spoken(n=14):     # tiếng ngắn 0,15 s, cao độ trượt, nghỉ phụ âm
        parts = []
        for _ in range(n):
            t = np.arange(int(0.15 * sr)) / sr
            f = np.linspace(rng.uniform(150, 260), rng.uniform(120, 220), len(t))
            parts += [tone([0], [0])[:0], np.sin(2 * np.pi * np.cumsum(f) / sr) * 0.3,
                      rng.normal(0, 0.02, int(0.08 * sr))]
        return np.concatenate(parts)

    a = sung()
    assert songs.note_share(a) > 0.6 and songs.note_share(spoken()) < 0.2
    # phim: 3 câu nói, 6 câu hát liền nhau (≈ 21 s), 1 câu nói kéo dài lẻ, 3 câu nói
    lines, audio, t = [], [], 0.0
    for kind in ["s", "s", "s"] + ["h"] * 6 + ["s", "x", "s", "s"]:
        clip = sung() if kind == "h" else spoken() if kind == "s" else tone([200], [1.2], 0.01)
        lines.append(Segment(t, t + len(clip) / sr, "你好"))
        audio += [clip, np.zeros(int(0.5 * sr))]
        t += len(clip) / sr + 0.5
    pcm = (np.concatenate(audio) * 32767).astype(np.int16)
    got = songs.guess_song_lines(lines, pcm)
    assert got == list(range(3, 9))                               # đúng 6 câu hát, câu kêu kéo dài lẻ (9…) không


def test_dub_tab_flags_undubbed_and_srt_row(monkeypatch):
    """Tab Lồng tiếng: bỏ cột 🎵 / Bỏ lồng (đổi bằng nút cho câu đã tích), Tích câu chưa lồng, ghi rõ giọng lấy từ
    đâu, khôi phục câu cảm thán; hàng xuất / nạp SRT chuyển sang tab Phụ đề."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami import pipeline
    from trami.gui.main import MainWindow
    from trami.gui.subs_model import C_NODUB, C_SONG, fit_text
    from trami.project import Project

    QApplication.instance() or QApplication([])
    p = Project.create("tab_long_tieng")
    segs = [Segment(2 * i, 2 * i + 1.5, f"句{i}", f"Câu {i}") for i in range(4)]
    segs[0].extra["dub_at"] = [0, 1.4]
    segs[2].extra["dub_at"] = [4, 5.4]
    p.segments = segs
    p.data["interj_removed"] = [Segment(7.6, 7.9, "啊", "A!").to_dict()]
    p.save()
    win = MainWindow()
    win.open_project(p.id)
    pp, m = win.project_page, win.project_page.model
    assert pp.dub_table.isColumnHidden(C_SONG) and pp.dub_table.isColumnHidden(C_NODUB)
    assert pp.s_srt_trans.text() == "Xuất SRT đã dịch"                      # hàng SRT ở tab Phụ đề
    assert pp.d_voice_note.text().startswith("Giọng đọc – bộ đọc:")
    toasts, jobs = [], []
    monkeypatch.setattr(win, "toast", lambda msg, kind="info", *a, **k: toasts.append(msg))
    monkeypatch.setattr(pipeline, "has_dub", lambda p: True)
    pp.submit = lambda kind, title, fn, **k: jobs.append((kind, title))
    pp._pick_undubbed()
    assert m.selected == {1, 3} and "2 câu chưa có giọng" in toasts[-1]
    m.set_picked(range(4), False)
    m.set_picked([1])
    pp._toggle_flag("song")
    assert not jobs and pp._relayout_go.isActive()          # xếp lại sau 1,2 s (gộp các thao tác liền nhau)
    pp._relayout_go.stop()
    pp._relayout_now()
    assert m.segs[1].extra.get("song") and jobs[-1][0] == "relayout" and fit_text(m.segs[1]) == "🎵 bài hát"
    pp._toggle_flag("song")
    assert not m.segs[1].extra.get("song") and m.segs[1].extra.get("not_song")
    pp._toggle_flag("nodub")
    assert m.segs[1].extra.get("nodub") and fit_text(m.segs[1]) == "bỏ lồng"
    pp._restore_interj()
    assert [s.trans for s in pp.model.segs] == ["Câu 0", "Câu 1", "Câu 2", "Câu 3", "A!"]
    assert pp.model.segs[-1].extra.get("keep_interj")
    # nút Lọc câu cảm thán: xoá ngay câu cảm thán lẻ (câu đã khôi phục thì giữ), cất để khôi phục
    from PySide6.QtWidgets import QMessageBox

    pp.model.segs.insert(1, Segment(1.6, 1.9, "嗯", "Ừm."))
    pp.model.segs[3].trans = "Ừm, đúng là như vậy."
    pp.model.set_segments(pp.model.segs)
    pp._drop_interj_now()
    assert [s.trans for s in pp.model.segs] == ["Câu 0", "Câu 1", "Ừm, đúng là như vậy.", "Câu 3", "A!"]
    assert [d["trans"] for d in Project(p.id).data["interj_removed"]] == ["Ừm."]
    # Xoá câu đã tích: hỏi lại rồi xoá cả phụ đề
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    pp.model.set_picked(range(len(pp.model.segs)), False)
    pp.model.set_picked([0, 3])
    pp._delete_picked()
    assert [s.trans for s in pp.model.segs] == ["Câu 1", "Ừm, đúng là như vậy.", "A!"]
    assert len(Project(p.id).segments) == 3
    win.close()


# ------------------------------------------------------------------ 2.0.62: nhiều API key, gói miễn phí, kỹ năng dịch chuẩn

def test_api_keys_rotate_and_free_tier_pacing(monkeypatch):
    """Key 1 hết lượt hôm nay / sai -> tự dùng key 2; gói miễn phí: không quá N lần gửi / phút mỗi key (nhiều key thì xen
    kẽ, 1 key thì chờ); hết sạch key -> báo rõ; số câu mỗi lần gửi riêng từng công cụ."""
    from trami import net, translate as tr

    assert tr.api_keys({"gemini_api_key": "A", "gemini_api_keys": ["A", " B ", "", "C"]}, "gemini") == ["A", "B", "C"]
    assert tr.engine_batch({"gemini_batch": 15, "translate_batch": 40}, "gemini") == 15
    assert tr.engine_batch({"translate_batch": 25}, "deepseek") == 25
    assert tr.engine_rpm({}, "gemini") == 8 and tr.engine_rpm({}, "deepseek") == 0

    class R:
        def __init__(self, code, text="", content='{"1": "ok"}'):
            self.status_code, self.text, self.headers, self._c = code, text, {}, content

        def json(self):
            return {"choices": [{"message": {"content": self._c}}]}

    plan: dict = {}
    sent: list = []

    class Sess:
        def post(self, url, headers=None, json=None, timeout=None):
            k = headers["Authorization"].split()[-1]
            sent.append(k)
            return plan[k](len(sent))

    monkeypatch.setattr(net, "session", lambda: Sess())
    slept: list = []
    monkeypatch.setattr(net, "sleep", lambda s, c=None: slept.append(s))
    for d in (tr._KEY_POS, tr._KEY_DEAD, tr._KEY_CALLS):
        d.clear()
    daily = '{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"quotaId": ' \
            '"GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}'
    plan.update(K1=lambda n: R(429, daily), K2=lambda n: R(200))
    st = {"gemini_api_keys": ["K1", "K2"], "gemini_rpm": 0}
    msgs: list = []
    with net.task(None, msgs.append):
        assert tr.LLMClient(st, "gemini").chat("s", "u") == '{"1": "ok"}'
    assert sent == ["K1", "K2"] and "hết lượt miễn phí hôm nay" in msgs[0] and "key Gemini số 2/2" in msgs[0]
    sent.clear()
    assert tr.LLMClient(st, "gemini").chat("s", "u") and sent == ["K2"]      # lần sau dùng luôn key còn lượt
    # key sai -> key sau
    for d in (tr._KEY_POS, tr._KEY_DEAD):
        d.clear()
    sent.clear()
    plan.update(K1=lambda n: R(400, "API key not valid. Please pass a valid API key."))
    assert tr.LLMClient(st, "gemini").chat("s", "u") and sent == ["K1", "K2"]
    # hết sạch key -> báo rõ
    tr._KEY_DEAD.clear()
    plan.update(K1=lambda n: R(429, daily), K2=lambda n: R(429, daily))
    with pytest.raises(tr.ApiError, match="Tất cả 2 key Gemini"):
        tr.LLMClient(st, "gemini").chat("s", "u")
    # giới hạn mỗi phút: 1 key 2 lần / phút -> lần 3 phải chờ; 2 key 1 lần / phút -> xen kẽ không chờ
    for d in (tr._KEY_POS, tr._KEY_DEAD, tr._KEY_CALLS):
        d.clear()
    plan.update(K1=lambda n: R(200), K2=lambda n: R(200))
    slept.clear()
    c = tr.LLMClient({"gemini_api_keys": ["K1"], "gemini_rpm": 2}, "gemini")
    for _ in range(3):
        c.chat("s", "u")
    assert len(slept) == 1 and 55 < slept[0] <= 61
    tr._KEY_CALLS.clear()
    slept.clear()
    sent.clear()
    c = tr.LLMClient({"gemini_api_keys": ["K1", "K2"], "gemini_rpm": 1}, "gemini")
    c.chat("s", "u")
    c.chat("s", "u")
    assert slept == [] and sorted(sent) == ["K1", "K2"]
    # 429 vì gửi quá nhanh (giới hạn mỗi phút), chỉ 1 key -> chờ theo "retry in" rồi gửi lại
    for d in (tr._KEY_POS, tr._KEY_DEAD, tr._KEY_CALLS):
        d.clear()
    slept.clear()
    plan.update(K1=lambda n: R(429, "Quota exceeded per minute. Please retry in 12.5s.") if n % 2 else R(200))
    sent.clear()
    assert tr.LLMClient({"gemini_api_keys": ["K1"], "gemini_rpm": 0}, "gemini").chat("s", "u")
    assert slept and 12 < slept[0] < 15
    # Translator dùng đúng số câu mỗi lần gửi của công cụ
    t = tr.Translator({"translate_engine": "gemini", "gemini_api_keys": ["K1"], "gemini_batch": 7}, "gemini")
    assert t.batch == 7
    for d in (tr._KEY_POS, tr._KEY_DEAD, tr._KEY_CALLS):
        d.clear()


def test_standard_skill_default_and_editable(tmp_path):
    """Kỹ năng 'Dịch chuẩn' (giữ đúng số câu, không gộp, tên riêng chuẩn) dùng mặc định, sửa được, về mặc định được;
    AI sửa lỗi nghe & nối câu không còn tự chạy trước khi dịch."""
    from trami import config, skills

    d = config.DEFAULT_SETTINGS
    assert d["translate_skill"] == skills.STD_ID and d["ai_clean_before_translate"] is False
    sk = skills.all_skills({})
    assert sk[0]["id"] == "chuan" and not sk[0]["builtin"] and "GIỮ ĐÚNG SỐ CÂU" in skills.skill_text(d)
    edited = {"translate_skills": [{"id": "chuan", "name": "Của tôi", "text": "- giữ đúng số câu, tên Hán Việt"}],
              "translate_skill": "chuan"}
    assert skills.skill_text(edited) == "- giữ đúng số câu, tên Hán Việt"
    assert [x["id"] for x in skills.all_skills(edited)].count("chuan") == 1


def test_jobs_run_concurrently_with_project_and_gpu_rules(monkeypatch):
    """Hàng đợi chạy nhiều việc cùng lúc: khác dự án chạy song song, cùng dự án lần lượt (trừ việc chỉ đọc), mỗi lúc
    chỉ 1 việc GPU; việc chờ ghi rõ vì sao."""
    import threading
    import time as _t

    from trami import gpu
    from trami.jobs import JobManager

    from trami import jobs as jobs_mod

    monkeypatch.setattr(gpu, "before_job", lambda kind: None)   # dọn card (gc) có test riêng – ở đây chỉ xét lịch chạy
    monkeypatch.setattr(gpu, "after_job", lambda kind: None)
    monkeypatch.setattr(jobs_mod, "max_running", lambda: 4)     # đã bật "Chạy nhiều việc cùng lúc" ở Cài đặt
    monkeypatch.setattr(gpu, "wait_for_vram", lambda *a, **k: "")

    m = JobManager(workers=4)
    live, peak, lock = set(), {}, threading.Lock()

    def make(name, dur=0.4):
        def fn(job):
            with lock:
                live.add(name)
                peak[name] = set(live)
            _t.sleep(dur)
            with lock:
                live.discard(name)
            return {"message": name}
        return fn

    jobs = [m.submit("translate", "dịch A", make("trA"), "A"),      # mạng
            m.submit("render", "xuất B", make("rdB"), "B"),          # CPU, dự án khác -> song song
            m.submit("recognize", "nghe C", make("gpuC"), "C"),      # GPU
            m.submit("dub", "lồng D", make("gpuD"), "D"),            # GPU thứ 2 -> chờ
            m.submit("render", "xuất A", make("rdA"), "A"),          # cùng dự án A -> chờ trA
            m.submit("preview", "xem trước A", make("pvA"), "A")]    # chỉ đọc -> chạy cùng trA
    _t.sleep(0.15)
    waiting = {j.title: j.message for j in jobs if j.status == "queued"}
    assert "card đồ hoạ" in waiting["lồng D"] and "dự án này" in waiting["xuất A"]
    t0 = _t.time()
    while any(j.status in ("queued", "running") for j in jobs) and _t.time() - t0 < 10:
        _t.sleep(0.05)
    assert all(j.status == "done" for j in jobs)
    assert {"trA", "rdB", "gpuC", "pvA"} <= peak["pvA"] | peak["trA"]       # 4 việc chạy cùng lúc
    assert "gpuC" not in peak["gpuD"] and "trA" not in peak["rdA"]
    assert m.counts() == (0, 0) and m.clear_finished() == 6 and m.list() == []


def test_progress_hook_per_thread():
    """2 việc cùng dùng thanh tqdm của thư viện: mỗi việc nhận đúng tiến độ của mình."""
    import threading

    tqdm = pytest.importorskip("tqdm")
    from trami.progress_hook import tqdm_progress

    got = {"a": [], "b": []}
    barrier = threading.Barrier(2)
    tqdm.tqdm.set_lock(threading.RLock())   # khoá chung của tqdm có thể bị test trước (tiến trình con) giữ lại

    def run(k, n):
        with tqdm_progress(lambda x, m: got[k].append(m), label=k):
            barrier.wait()
            bar = tqdm.tqdm(total=n, disable=False, file=open(os.devnull, "w"), mininterval=0)
            for _ in range(n):
                bar.update(1)
            bar.close()

    ts = [threading.Thread(target=run, args=("a", 7)), threading.Thread(target=run, args=("b", 9))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert got["a"] and all(m.startswith("a ") and m.endswith("/7") for m in got["a"])
    assert got["b"] and all(m.startswith("b ") and m.endswith("/9") for m in got["b"])


def test_qwen_streams_chunk_groups(monkeypatch, tmp_path):
    """Vừa tách vừa dịch: Qwen3-ASR nghe theo nhóm khúc, nghe xong nhóm nào gửi câu nhóm đó ngay."""
    from trami import asr_qwen

    calls, got = [], []

    class W:
        def call(self, req, progress=None, cancel=None):
            if req.get("cmd") == "align":
                calls.append(("align", len(req["items"])))
                return {"items": [{"words": [[ch, 0.1 * i, 0.1 * i + 0.08] for i, ch in enumerate(t[:-1])]}
                                  for _a, _b, t in req["items"]]}
            calls.append((len(req["spans"]), req.get("align")))
            if progress:
                progress(1.0, "nghe")
            return {"items": [{"text": f"第{a:.0f}句话。", "words": []} for a, _ in req["spans"]]}

    monkeypatch.setattr(asr_qwen, "worker", lambda st: W())
    monkeypatch.setattr(asr_qwen, "_spans", lambda audio, st: [(i * 3.0, i * 3.0 + 2.0) for i in range(14)])
    monkeypatch.setattr("trami.asr_funasr.load_wav16k", lambda w: np.zeros(16000 * 45, np.float32))
    monkeypatch.setattr("trami.align.chunk_spans", lambda spans, audio, max_gap, max_len: spans)
    segs = asr_qwen.transcribe_qwen(tmp_path / "a.wav", {}, "zh", None, None, on_chunk=lambda s: got.append(len(s)))
    # nghe theo nhóm KHÔNG căn (bộ căn không nằm trên card lúc đang nghe), nghe xong căn 1 lần cả phim
    assert calls == [(6, False), (6, False), (2, False), ("align", 14)] and got == [6, 6, 2] and len(segs) == 14
    assert all(s.extra.get("ct") for s in segs)                 # câu cuối cùng có mốc từng chữ
    calls.clear()
    asr_qwen.transcribe_qwen(tmp_path / "a.wav", {}, "zh", None, None)
    assert calls == [(14, False), ("align", 14)]                # chạy thường: nghe hết -> (nghe lại chỗ cắt) -> căn


def test_auto_parallel_translates_and_reads_while_listening(monkeypatch):
    """Tab Tự động "vừa tách vừa dịch": câu nghe xong được dịch theo từng lô (đủ số câu mỗi lần gửi), hiện ngay trong
    file câu nháp, Edge đọc trước; nghe xong chỉ dịch / đọc lại câu bị đổi; giọng chọn ở tab Tự động áp cho mọi người."""
    import threading
    import time as _t

    from trami import pipeline, stream, tts
    from trami import translate as tr
    from trami.config import save_settings
    from trami.project import Project

    save_settings({"translate_batch": 3, "tts_voice": "vi-VN-HoaiMyNeural"})
    sent, spoken, seen = [], [], []

    def fake_google(self, lines):
        sent.append(list(lines))
        return [f"dịch {len(l)} {l[1]}" for l in lines]

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, path, voice in items:
            spoken.append((text, voice))
            _fake_audio(path, 0.5, 300)
        return []

    monkeypatch.setattr(tr.Translator, "_google", fake_google)
    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    monkeypatch.setattr(stream, "TTS_WAIT", 0.0)
    texts = [f"第{i}句话" for i in range(9)]

    def fake_recognize(p, method, language, region, ocr_fps, settings, progress, cancel, denoise, on_draft=None):
        for g in range(3):          # 3 nhóm khúc, mỗi nhóm 3 câu
            on_draft([Segment(g * 9 + k * 3.0, g * 9 + k * 3.0 + 2.0, texts[g * 3 + k]) for k in range(3)])
            t0 = _t.time()
            while (len(sent) <= g or len(spoken) < 3 * (g + 1)) and _t.time() - t0 < 5:
                _t.sleep(0.02)
            seen.append(len(stream.read_draft(p.work) or []))
            progress(0.5, "nghe")
        final = [Segment(g * 9 + k * 3.0, g * 9 + k * 3.0 + 2.0, texts[g * 3 + k], "", f"S{k % 2 + 1}")
                 for g in range(3) for k in range(3)]
        final[4].text = "第4句话吗"                             # câu bị đổi sau khi nghe lại / nối câu
        p.segments = final
        p.save()
        return len(final)

    monkeypatch.setattr(pipeline, "recognize", fake_recognize)
    p = Project.create("song song")
    p.data["info"] = {"duration": 30}
    p.save()
    opts = {"engine": "google", "dub": True, "tts_engine": "edge", "parallel": True, "render": False,
            "voice": "edge:vi-VN-NamMinhNeural", "hardsub_mode": "none", "which": "trans"}
    res = pipeline.auto(p, opts, None, None)
    p.reload()
    assert seen == [3, 6, 9]                                     # câu nháp hiện dần
    assert [len(b) for b in sent[:3]] == [3, 3, 3]              # gửi đúng lô 3 câu lúc đang nghe
    assert sum(len(b) for b in sent) == 10 and sent[-1] == ["第4句话吗"]   # nghe xong: chỉ dịch câu bị đổi
    assert p.data["live_reused"] == 8 and "dùng lại 8 câu" in res["message"]
    assert all(s.trans for s in p.segments)
    assert set(p.data["voices"].values()) == {"edge:vi-VN-NamMinhNeural"}
    # Edge: 9 câu đọc trước + đúng 1 câu bị đổi đọc ở bước lồng tiếng
    assert len(spoken) == 10 and {v for _, v in spoken} == {"vi-VN-NamMinhNeural"}
    assert [t for t, _ in spoken].count("Dịch 5 4") == 1
    assert not stream.draft_path(p.work).exists()               # dọn file câu nháp
    assert threading.active_count() < 50


def test_dub_voices_never_silently_single(monkeypatch):
    """Nhiều người nói chưa chọn giọng -> tự gán mỗi người 1 giọng (trước: cả phim 1 giọng mặc định); giọng chọn mà bộ
    đọc chưa cài -> báo rõ thay vì đổi im lặng sang Hoài My; báo giọng thật từng người nói; chọn giọng ở tab Tự động bỏ
    giọng riêng từng câu (giọng riêng đè lên giọng người nói)."""
    from trami import pipeline, tts
    from trami.project import Project

    spoken = []

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            spoken.append((i, voice))
            _fake_audio(p, 0.5, 300)
        return []

    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    monkeypatch.setattr(tts, "vieneu_available", lambda: False)
    monkeypatch.setattr(pipeline, "speaker_genders", lambda p, audio=None: {})
    p = Project.create("nhiều giọng")
    p.data["info"] = {"duration": 6}
    p.segments = [Segment(0.2, 1.0, "a", "Một", "S1"), Segment(1.5, 2.3, "b", "Hai", "S2"),
                  Segment(3.0, 3.8, "c", "Ba", "S1")]
    p.save()
    st = {"translate_engine": "google", "tts_voice": "vi-VN-HoaiMyNeural"}
    pipeline.dub(p, None, settings=st, engine="edge")
    v = {i: voice for i, voice in spoken}
    assert v[0] == v[2] != v[1]                                     # 2 người nói, 2 giọng
    assert len(set(p.data["voices"].values())) == 2
    msg = pipeline.dub_summary(p)
    assert "tự gán giọng khác nhau cho 2 người nói" in msg and "S1:" in msg and "S2:" in msg
    # chọn giọng VieNeu nhưng máy chưa cài VieNeu -> báo rõ
    p.data["voices"] = {"S1": "vieneu:Bình", "S2": "edge:vi-VN-NamMinhNeural"}
    p.save()
    pipeline.dub(p, None, settings=st, engine="edge")
    assert "chọn giọng Bình (VieNeu) nhưng bộ đọc này chưa cài" in pipeline.dub_summary(p)
    # giọng riêng của câu bị bỏ khi chọn 1 giọng cho mọi người ở tab Tự động
    segs = p.segments
    segs[0].extra["voice"] = "edge:vi-VN-HoaiMyNeural"
    p.segments = segs
    p.save()
    pipeline._auto_voices(p, {"voice": "edge:vi-VN-NamMinhNeural"}, st)
    assert set(p.data["voices"].values()) == {"edge:vi-VN-NamMinhNeural"}
    assert not any(s.extra.get("voice") for s in p.segments)


def test_one_voice_for_all_applies_to_every_dub_button(monkeypatch):
    """"Giọng cho tất cả" (tab Tự động / mục 2): mọi lần lồng tiếng (đầy đủ, tạo lại giọng câu đã tích) đọc đúng giọng đó
    cho mọi người nói; dự án mới lấy mặc định ở Cài đặt; dự án đã chọn giọng từng người ("") thì theo mục 2."""
    from trami import pipeline, tts
    from trami.project import Project

    spoken = []

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            spoken.append(voice)
            _fake_audio(p, 0.5, 300)
        return []

    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    p = Project.create("một giọng")
    p.data["info"] = {"duration": 6}
    p.segments = [Segment(0.2, 1.0, "a", "Một", "S1"), Segment(1.5, 2.3, "b", "Hai", "S2")]
    p.save()
    st = {"translate_engine": "google", "tts_voice": "vi-VN-HoaiMyNeural", "dub_voice_all": "edge:vi-VN-NamMinhNeural"}
    pipeline.dub(p, None, settings=st, engine="edge")
    assert spoken == ["vi-VN-NamMinhNeural"] * 2 and set(p.data["voices"].values()) == {"edge:vi-VN-NamMinhNeural"}
    spoken.clear()
    pipeline.redub(p, [1], settings=st, engine="edge")
    assert spoken == ["vi-VN-NamMinhNeural"]
    assert p.data["voice_all"] == "edge:vi-VN-NamMinhNeural"     # dự án nhớ giọng chung đã dùng
    # dự án CŨ (trước 2.0.66) đã chọn giọng từng người, chưa có voice_all -> mặc định chung ở Cài đặt không đè lên
    q = Project.create("dự án cũ")
    q.data["info"] = {"duration": 6}
    q.segments = [Segment(0.2, 1.0, "a", "Một", "S1"), Segment(1.5, 2.3, "b", "Hai", "S2")]
    q.data["voices"] = {"S1": "edge:vi-VN-HoaiMyNeural", "S2": "edge:vi-VN-HoaiMyNeural"}
    q.save()
    spoken.clear()
    pipeline.dub(q, None, settings=st, engine="edge")
    assert spoken == ["vi-VN-HoaiMyNeural"] * 2 and "voice_all" not in q.data
    # dự án chọn giọng từng người -> không theo mặc định chung
    p.data["voice_all"] = ""
    p.data["voices"] = {"S1": "edge:vi-VN-HoaiMyNeural", "S2": "edge:vi-VN-NamMinhNeural"}
    p.save()
    spoken.clear()
    pipeline.redub(p, [0, 1], settings=st, engine="edge")
    assert spoken == ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]


def test_gpu_memory_released_between_jobs(monkeypatch):
    """Card đồ hoạ: trước việc GPU tắt tiến trình AI việc đó không dùng (đang đọc dở thì để nguyên), sau việc GPU bỏ mô
    hình trong tiến trình giao diện; tiến trình đọc giọng để lâu tự tắt; xuất video còn phải xoá phụ đề AI là việc GPU."""
    import threading
    import time as _t

    from trami import gpu, jobs, lama, pipeline, tts
    from trami import asr_qwen
    from trami.project import Project

    class FakeProc:
        def __init__(self):
            self.alive = True

        def poll(self):
            return None if self.alive else 0

    class W:
        def __init__(self):
            self.proc, self.lock, self.last_used, self.stopped = FakeProc(), threading.Lock(), _t.time(), 0

        def stop(self):
            self.stopped += 1
            self.proc.alive = False

    vn, qw = W(), W()
    monkeypatch.setattr(tts, "_vn_model", vn)
    monkeypatch.setattr(tts, "_omni_model", None)
    monkeypatch.setattr(asr_qwen, "_worker", qw)
    monkeypatch.setattr(lama, "_model", object())
    gpu.before_job("recognize")                     # nghe: tắt VieNeu, giữ Qwen, bỏ LaMa trong giao diện
    assert vn.stopped == 1 and qw.stopped == 0 and lama._model is None
    vn.proc.alive = True
    with vn.lock:                                   # VieNeu đang đọc dở (nghe thử) -> không tắt ngang
        gpu.before_job("separate")
    assert vn.stopped == 1
    gpu.before_job("dub")                           # lồng tiếng: tắt Qwen, giữ VieNeu
    assert qw.stopped == 1 and vn.stopped == 1
    vn.last_used = _t.time() - gpu.IDLE_STOP - 5    # để không quá lâu -> tự tắt
    assert gpu.reap_idle() == 1 and vn.stopped == 2

    p = Project.create("xuất xoá AI")
    p.data["source"] = __file__
    e = p.edit
    e["hardsub"] = {"mode": "inpaint", "regions": [{"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1}]}
    p.data["edit"] = e
    p.save()
    monkeypatch.setattr(pipeline, "inpainted_video", lambda p, hs=None, settings=None: None)
    assert jobs.needs_gpu("render", p.id)           # còn phải xoá phụ đề bằng AI -> chờ việc GPU khác
    monkeypatch.setattr(pipeline, "inpainted_video", lambda p, hs=None, settings=None: "x.mp4")
    assert not jobs.needs_gpu("render", p.id)       # đã xoá sẵn -> chỉ ghép video, chạy song song được
    assert jobs.needs_gpu("recognize") and not jobs.needs_gpu("download")


def test_trend_voices_vieneu(monkeypatch, tmp_path):
    """20 giọng VieNeu theo trend: hiệu ứng giữ thời lượng / đổi đúng cao độ, đọc bằng giọng gốc + kiểu đọc riêng,
    file 24 kHz, đổi mô tả giọng thì đọc lại, câu tiếng khác đọc bằng Edge cùng nam / nữ."""
    from trami import gender, pipeline, trend_voices as tv, tts, voicefx as fx

    sr = 24000
    a = _speech_like(sr, 2.0)

    def f0(x):
        b = fx.resample_len(x, int(len(x) * 16000 / sr))
        p = gender.pitch_track(b, 16000)
        return float(np.median(p[p > 0]))
    for p, fm in ((3, 1.5), (-2, -1.5), (5, None)):
        y = fx.shift(a, sr, p, fm)
        assert len(y) == len(a) and abs(f0(y) / f0(a) - 2 ** (p / 12)) < 0.04
    assert abs(len(fx.stretch(a, 1.15, sr)) - len(a) / 1.15) < 2 and abs(f0(fx.stretch(a, 0.9, sr)) / f0(a) - 1) < 0.02
    lv = tts.speech_level(a, sr)
    for p in tv.PRESETS:   # mọi giọng: không vỡ tiếng, cùng độ to, đuôi vang ≤ 0,4 s
        y = tv.process(tv.PREFIX + p["id"], a, sr)
        assert np.isfinite(y).all() and np.abs(y).max() <= 0.891
        body = len(a) / p["fx"].get("tempo", 1.0)
        assert body - 2 <= len(y) <= body + fx.TAIL_MAX * sr + 2
        assert abs(tts.speech_level(y, sr) / lv - 1) < 0.15 or np.abs(y).max() > 0.88
    assert len(tv.PRESETS) == 20 and len({p["id"] for p in tv.PRESETS}) == 20
    assert tv.base_voice("vieneu:trend:robot", [{"id": "vieneu:X", "gender": "female"},
                                                {"id": "vieneu:Y", "gender": "male"}]) == "Y"   # bản Nano: cùng nam
    assert pipeline.voice_label("vieneu:trend:robot") == "Robot AI (VieNeu)"

    calls = []

    class FakeVieneu:
        sr = 48000

        def ensure_started(self, progress=None, cancel=None):
            pass

        def synth(self, text, out, voice=None, ref_audio=None, params=None, **kw):
            calls.append((text, voice, dict(params or {})))
            return tts.write_wav(out, _speech_like(48000, 1.2), 48000)
    got = {}

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            got[i] = voice
            _fake_audio(p, 0.6)
        return []
    presets = [{"id": "vieneu:Quốc Tuấn", "name": "Quốc Tuấn", "gender": "male"},
               {"id": "vieneu:Hải Đăng", "name": "Hải Đăng", "gender": "male"},
               {"id": "vieneu:Trúc Ly", "name": "Trúc Ly", "gender": "female"}]
    monkeypatch.setattr(tts, "vieneu_available", lambda: True)
    monkeypatch.setattr(tts, "vieneu_presets", lambda mode="v3turbo": presets)
    monkeypatch.setattr(tts, "vieneu_sample_voices", lambda: [])
    monkeypatch.setattr(tts, "_vieneu", lambda mode: FakeVieneu())
    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    assert any(v["id"] == "vieneu:trend:de_thuong" and v.get("tip") for v in tts.list_voices({}, "vi"))
    segs = [Segment(0, 1.5, "a", "Xin chào cả nhà", "S1"), Segment(2, 3.5, "b", "Hôm nay vui quá", "S2"),
            Segment(4, 5, "c", "สวัสดีครับ", "S1"), Segment(5.5, 7, "d", "Mua ngay nhé", "S3")]
    voices = {"S1": "vieneu:trend:robot", "S2": "vieneu:trend:de_thuong", "S3": "vieneu:trend:mc_soi_dong"}
    stats = {}
    tts.synthesize_dub(segs, tmp_path / "dub.wav", 8.0, tmp_path / "t", voices=voices, stats=stats)
    by_text = {c[0]: c for c in calls}
    assert by_text["Xin chào cả nhà"][1] == "Quốc Tuấn" and by_text["Hôm nay vui quá"][1] == "Trúc Ly"
    assert by_text["Mua ngay nhé"][2]["temperature"] == 0.85          # MC: kiểu đọc biểu cảm riêng
    assert by_text["Xin chào cả nhà"][2] == tts.vieneu_params({})     # còn lại theo Cài đặt
    assert got[2] == "th-TH-NiwatNeural" and "สวัสดีครับ" not in by_text   # robot (nam) -> Edge nam tiếng Thái
    for s in (segs[0], segs[1], segs[3]):
        assert tts.read_wav_rate(s.extra["tts_file"]) == 24000
    assert stats["placed"] == 4 and stats["voices_used"]["S2"] == {"vieneu:trend:de_thuong": 1}
    n = len(calls)
    tts.synthesize_dub(segs, tmp_path / "dub2.wav", 8.0, tmp_path / "t", voices=voices)
    assert len(calls) == n                                             # đã đọc rồi: dùng lại file
    p = tv.get("de_thuong")
    monkeypatch.setitem(p, "fx", dict(p["fx"], pitch=4.0))
    tts.synthesize_dub(segs, tmp_path / "dub3.wav", 8.0, tmp_path / "t", voices=voices)
    assert [c[0] for c in calls[n:]] == ["Hôm nay vui quá"]            # đổi hiệu ứng: chỉ đọc lại giọng đó
    out = tts.preview_voice("vieneu:trend:soc_chuot", "Xin chào", tmp_path / "pv", {})
    assert out.exists() and tts.read_wav_rate(out) == 24000


@pytest.mark.parametrize("model", ["vocals_mel_band_roformer.ckpt",
                                   "ens:model_bs_roformer_ep_317_sdr_12.9755.ckpt+vocals_mel_band_roformer.ckpt"])
def test_separate_long_audio_in_chunks(tmp_path, monkeypatch, model):
    """Phim dài: tách từng đoạn (mỗi mô hình nạp 1 lần), nối mượt chỗ chồng, đúng độ dài; mép đoạn tách kém (thư viện
    làm mép xấu) không lộ ra vì vùng nối bỏ mép; kết hợp 2 mô hình = trung bình."""
    import types
    import wave as wv

    from trami import separate as sp

    monkeypatch.setattr(sp, "CHUNK_SEC", 4.0)
    monkeypatch.setattr(sp, "OVERLAP_SEC", 1.5)
    monkeypatch.setattr(sp, "MARGIN_SEC", 0.4)
    sr = 44100
    t = np.arange(int(13.3 * sr)) / sr
    mix = np.stack([0.5 * np.sin(2 * np.pi * (200 + 30 * t) * t), 0.4 * np.sin(2 * np.pi * 310 * t)], 1)
    src = tmp_path / "a.wav"
    with wv.open(str(src), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((mix * 32767).astype(np.int16).tobytes())
    calls = {"init": 0, "load": [], "sep": 0}
    gain = {sp.BS_ROFORMER: 0.6, sp.KIM_ROFORMER: 0.8}

    class FakeSep:
        def __init__(self, output_dir, model_file_dir, output_format, log_level, **kw):
            self.out = Path(output_dir)
            calls["init"] += 1
            assert not kw                       # tự kết hợp, không cần bản thư viện có ensemble

        def load_model(self, model_filename):
            calls["load"].append(model_filename)
            self.g = gain[model_filename]

        def separate(self, path, names):
            calls["sep"] += 1
            with wv.open(path, "rb") as r:
                a = np.frombuffer(r.readframes(r.getnframes()), np.int16).reshape(-1, 2).astype(np.float32)
            edge = int(0.3 * sr)
            for name, g in (("vocals", self.g), ("instrumental", 1 - self.g)):
                y = a * g
                y[:edge] = 0
                y[-edge:] = 0                     # mép đoạn tách kém
                with wv.open(str(self.out / f"{name}.wav"), "wb") as w:
                    w.setnchannels(2), w.setsampwidth(2), w.setframerate(sr)
                    w.writeframes(y.astype(np.int16).tobytes())
            return ["vocals.wav", "instrumental.wav"]

    mod = types.ModuleType("audio_separator.separator")
    mod.Separator = FakeSep
    monkeypatch.setitem(sys.modules, "audio_separator", types.ModuleType("audio_separator"))
    monkeypatch.setitem(sys.modules, "audio_separator.separator", mod)
    monkeypatch.setattr(sp, "available", lambda: True)
    chunks = sp.plan_chunks(len(t))
    assert len(chunks) == 3 and chunks[0][0] == 0 and chunks[-1][1] == len(t)
    assert all(b > c for (_, b), (c, _) in zip(chunks, chunks[1:]))          # 2 đoạn liền luôn chồng nhau
    v, i = sp.separate(src, tmp_path / "stems", model)
    models = sp.model_list(model)
    assert calls["init"] == len(models) and calls["load"] == models and calls["sep"] == 3 * len(models)
    g = np.mean([gain[m] for m in models])
    for f, want in ((v, g), (i, 1 - g)):
        with wv.open(str(f), "rb") as r:
            y = np.frombuffer(r.readframes(r.getnframes()), np.int16).reshape(-1, 2) / 32767
        assert len(y) == len(t)
        inner = slice(int(0.3 * sr), len(t) - int(0.3 * sr))   # mép đầu / cuối cả bài vốn bị fake làm câm
        assert np.abs(y[inner] - mix[inner] * want).max() < 0.002
    assert (tmp_path / "stems" / "model.txt").read_text("utf-8") == model
    assert not (tmp_path / "stems" / "_sep").exists() and not (tmp_path / "stems" / "mix44k.wav").exists()


def test_jobs_sequential_by_default_and_qwen_batches(monkeypatch):
    """Mặc định chạy LẦN LƯỢT từng việc (như trước 2.0.64); bộ nghe Qwen3 chia lô theo độ dài, card 8 GB lô nhỏ +
    chỉ 1 mô hình trên card."""
    import threading
    import time as _t

    from trami import gpu, jobs as jobs_mod
    from trami.config import DEFAULT_SETTINGS
    from trami.jobs import JobManager
    from trami.qwen_asr_worker import plan_batches, vram_plan

    assert DEFAULT_SETTINGS["jobs_parallel"] is False and DEFAULT_SETTINGS["auto_parallel"] is False
    monkeypatch.setattr(jobs_mod, "_limit_cache", [0.0, 1])
    monkeypatch.setattr(gpu, "before_job", lambda kind: None)
    monkeypatch.setattr(gpu, "after_job", lambda kind: None)
    monkeypatch.setattr(gpu, "wait_for_vram", lambda *a, **k: "")
    assert jobs_mod.max_running() == 1
    m = JobManager(workers=4)
    gate, live, peak = threading.Event(), [0], [0]

    def fn(j):
        live[0] += 1
        peak[0] = max(peak[0], live[0])
        gate.wait(2)
        live[0] -= 1
    a = m.submit("translate", "A", fn, "p1")
    b = m.submit("update", "B", fn, "p2")            # khác dự án, không dùng GPU – vẫn phải chờ (tải video thì không:
    #                                                   PREFETCH_KINDS, xem test_autoflow_links_preset_prefetch)
    _t.sleep(0.4)
    assert m.jobs[b.id].status == "queued" and "lần lượt" in m.jobs[b.id].message
    gate.set()
    t0 = _t.time()
    while any(m.jobs[x.id].status in ("queued", "running") for x in (a, b)) and _t.time() - t0 < 5:
        _t.sleep(0.05)
    assert peak[0] == 1 and m.jobs[b.id].status == "done"
    lens = [28, 3, 27, 5, 10, 28, 2, 9, 15, 26]
    budget, one = vram_plan(8 * 1024 ** 3)
    assert one and budget <= 60
    bs = plan_batches(lens, list(range(len(lens))), budget)
    assert sorted(k for x in bs for k in x) == list(range(len(lens)))
    assert all(max(lens[k] for k in x) * len(x) <= budget or len(x) == 1 for x in bs)
    assert vram_plan(24 * 1024 ** 3) == (224.0, False)


def test_vieneu_slow_card_restarts_and_heartbeat(tmp_path, monkeypatch):
    """VieNeu chậm dần như card đầy (Windows lấy RAM thay, GPU vẫn 98 %): câu chậm bất thường -> mở lại bộ đọc trước câu
    sau; câu đang đọc lâu -> dòng tiến độ đếm giây; bộ đọc treo hẳn -> thời gian chờ theo tốc độ thật, không đợi 5 phút."""
    import queue
    import textwrap
    import time

    from trami import tts

    script = tmp_path / "fakevn.py"
    script.write_text(textwrap.dedent("""
        import json, sys, time
        M = "@@TRAMI@@"
        print(M + json.dumps({"ready": True, "sr": 24000}), flush=True)
        for line in sys.stdin:
            req = json.loads(line)
            t = req["text"]
            time.sleep(1.6 if "slow" in t else 30 if "hang" in t else 0.02)
            print(M + json.dumps({"ok": True, "sr": 24000, "vram_free": 0.03}), flush=True)
    """), "utf-8")
    starts = []

    class W(tts.VieneuWorker):
        stall_min, SLOW_MIN = 3.0, 1.0       # ngưỡng thật 60 s / 20 s – hạ cho test chạy nhanh

        def _cmd(self):
            starts.append(1)
            return [sys.executable, str(script)]
    w = W("v3turbo")
    for k in range(6):                       # tốc độ thường ~0,02 s / câu
        w.synth(f"normal line {k}", tmp_path / "a.wav")
    assert len(starts) == 1 and 0 < w._typical(20) < 0.5 and w._stall_for("x" * 20) == 3.0
    w.synth("slow line", tmp_path / "a.wav")
    assert "mở lại bộ đọc" in w.note and "3%" in w.note and w.proc is None   # câu chậm -> đã tắt tiến trình
    w.synth("normal again", tmp_path / "a.wav")
    assert len(starts) == 2                  # câu sau mở lại tiến trình (card sạch)
    t0 = time.time()
    with pytest.raises(RuntimeError, match="3 giây"):   # treo hẳn: chờ 3 s (không phải 5 phút), mở lại, đọc lại 1 lần
        w.synth("hang line", tmp_path / "a.wav")
    assert time.time() - t0 < 15 and len(starts) == 3
    w.stop()
    # câu đọc lâu: on_wait được gọi khi đã chờ > 10 s (đồng hồ giả)
    w4 = W("v3turbo")
    w4.proc = type("P", (), {"poll": lambda self: None})()
    w4._q = queue.Queue()
    now = [1000.0]
    monkeypatch.setattr(tts.time, "time", lambda: now[0])

    def get(timeout=0.2):
        if now[0] < 1030:
            now[0] += 6
            raise queue.Empty
        return '@@TRAMI@@{"ok": true}\n'
    w4._q.get = get
    beats: list = []
    assert w4._read(stall=100, on_wait=beats.append) == {"ok": True}
    assert beats and beats[0] >= 10


def test_vieneu_worker_vram_guard(monkeypatch):
    """Tiến trình VieNeu: card còn trống < 15 % -> bỏ các đồ thị CUDA đã chụp + trả bộ nhớ đệm; card 8 GB -> đọc từng
    đoạn một (batch 1)."""
    import types

    from trami import vieneu_worker as vw

    state = {"free": 0.5 * 2 ** 30, "emptied": 0}
    cuda = types.SimpleNamespace(
        is_available=lambda: True, mem_get_info=lambda: (state["free"], 8 * 2 ** 30),
        synchronize=lambda: None,
        empty_cache=lambda: (state.__setitem__("emptied", state["emptied"] + 1),
                             state.__setitem__("free", 3 * 2 ** 30)),
        get_device_properties=lambda i: types.SimpleNamespace(total_memory=8 * 2 ** 30))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=cuda))
    eng = types.SimpleNamespace(_fused={(1, 512): 1, (4, 1024): 2, (8, 1024): 3}, _graphs={(1,): 1})
    tts_obj = types.SimpleNamespace(_batch_engine=eng)
    assert vw._small_card()
    out = vw._vram_guard(tts_obj)
    assert out["freed_graphs"] == 4 and not eng._fused and not eng._graphs and state["emptied"] == 1
    assert out["vram_free"] == 0.375
    eng._fused[(1, 512)] = 1
    assert vw._vram_guard(tts_obj) == {"vram_free": 0.375} and eng._fused   # còn nhiều chỗ: giữ đồ thị (nhanh)


def test_redub_resumes_after_cancel(monkeypatch):
    """Huỷ "Tạo lại giọng" giữa chừng rồi bấm lại: không đánh lần đọc mới lần nữa -> câu đã đọc xong được dùng lại;
    chạy xong rồi bấm lại mới là lần đọc mới."""
    from trami import pipeline
    from trami.ffmpeg import Cancelled
    from trami.project import Project

    p = Project.create("redub-resume")
    p.segments = [Segment(i, i + 0.8, f"句{i}", f"Câu {i}") for i in range(4)]
    p.save()
    calls = {"n": 0}

    def fake_dub(p, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Cancelled()
        return Path("dub.wav")
    monkeypatch.setattr(pipeline, "dub", fake_dub)
    with pytest.raises(Cancelled):
        pipeline.redub(p, [0, 1, 2])
    q = Project(p.id)
    assert [s.extra.get("take") for s in q.segments] == [1, 1, 1, None]
    pipeline.redub(q, [0, 1, 2])                 # bấm lại sau khi huỷ: vẫn lần đọc 1 (dùng lại file đã đọc)
    q = Project(q.id)
    assert [s.extra.get("take") for s in q.segments] == [1, 1, 1, None]
    assert not any(s.extra.get("take_pending") for s in q.segments)
    pipeline.redub(q, [1])                       # đã xong -> bấm lại = lần đọc mới
    assert [s.extra.get("take") for s in Project(q.id).segments] == [1, 2, 1, None]
    p.delete()


def test_relayout_patches_only_changed_part(monkeypatch, tmp_path):
    """Xoá câu / đọc lại 1 câu trên bản lồng đã có: chỉ ghi lại đúng đoạn thời gian thay đổi vào file bản lồng (kết quả
    y hệt dựng lại cả bản), file nghe thử của trình phát được vá theo (khớp từng mẫu, không giải mã lại cả phim), giọng
    đã tua nhanh lưu đệm (xếp lại không gọi FFmpeg cho từng câu); file khác định dạng -> dựng lại cả bản như cũ."""
    import numpy as np

    from trami import audiomix, pipeline, tts
    from trami.project import Project

    reads = []

    async def fake_edge(items, rate, pitch, progress, cancel, total, done0, concurrency=6):
        for i, text, p, voice in items:
            reads.append(i)
            n = int(tts.SR * (0.3 + 0.06 * len(text)))
            x = np.sin(np.arange(n) * 2 * np.pi * (200 + 9 * len(text) + 40 * len(reads)) / tts.SR) * 0.3
            tts.write_wav(p, x.astype(np.float32))
        return []

    monkeypatch.setattr(tts, "_edge_all", fake_edge)
    p = Project.create("vá bản lồng")
    p.data["info"] = {"duration": 40}
    segs, t = [], 0.5
    for i in range(16):
        text = "Câu thoại số " + "dài " * (2 * (i % 5)) + str(i)
        segs.append(Segment(round(t, 2), round(t + 1.2, 2), f"句{i}", text, "S1"))
        t += 2.2
    p.segments = segs
    p.save()
    st = {"translate_engine": "google", "tts_voice": "vi-VN-HoaiMyNeural", "dub_level_match": False}
    wav = pipeline.dub(p, None, settings=st, engine="edge")
    ver = pipeline.dub_versions(p)[0]
    assert len(ver["placed"]) == 16 and ver.get("env_top")
    assert any(e[2] != 1.0 for e in ver["placed"])               # có câu phải đọc nhanh
    raw0 = audiomix.decode_track(wav, p.work / "live", "dub")
    calls = []
    real = tts._decode
    monkeypatch.setattr(tts, "_decode", lambda *a, **k: calls.append(a) or real(*a, **k))
    before = tts.read_wav(wav)[0].copy()
    segs = p.segments
    del segs[7]                                                   # câu 7: 15,9 s
    p.segments = segs
    p.save()
    assert pipeline.relayout(p, st) == wav
    after = tts.read_wav(wav)[0]
    diff = np.where(np.abs(after - before) > 1e-4)[0] / tts.SR
    assert len(diff) and diff[0] > 13 and diff[-1] < 26           # chỉ quanh câu đã xoá (+ cụm câu dài sát sau)
    assert len(calls) <= 3                  # chỉ câu sát bên đổi tốc độ mới phải tua lại, câu khác dùng bản đệm
    calls.clear()
    mt = wav.stat().st_mtime_ns
    pipeline.relayout(p, st)                                      # không đổi gì: không tua, không ghi file
    assert not calls and wav.stat().st_mtime_ns == mt
    ref = tmp_path / "ref.wav"
    tts.synthesize_dub(p.segments, ref, 40, p.work / "tts", "edge:vi-VN-HoaiMyNeural", settings=st, max_speedup=1.3,
                       stats={}, only=set())
    assert np.abs(tts.read_wav(ref)[0] - after).max() == 0       # y hệt dựng lại cả bản
    raw1 = audiomix.decode_track(wav, p.work / "live", "dub")
    fresh = audiomix.decode_track(wav, tmp_path / "fresh", "dub")
    assert raw1 == raw0 and np.array_equal(np.fromfile(raw1, np.int16), np.fromfile(fresh, np.int16))
    env = np.load(pipeline.dub_env_path(wav)).astype(np.float32)
    want_env = tts.wave_envelope(after, tts.SR).astype(np.float32)
    assert len(env) == len(want_env) and np.abs(env - want_env).max() < 0.01
    # đọc lại 1 câu: chỉ câu đó được đọc + vá đúng chỗ của nó, trình phát vẫn dùng bản đã vá
    before = after.copy()
    reads.clear()
    pipeline.redub(p, [2], settings=st, engine="edge")
    assert reads == [2]
    after = tts.read_wav(wav)[0]
    diff = np.where(np.abs(after - before) > 1e-4)[0] / tts.SR
    assert len(diff) and diff[0] > 3.5 and diff[-1] < 9
    raw2 = audiomix.decode_track(wav, p.work / "live", "dub")
    fresh = audiomix.decode_track(wav, tmp_path / "fresh2", "dub")
    assert raw2 == raw0 and np.array_equal(np.fromfile(raw2, np.int16), np.fromfile(fresh, np.int16))
    # bản lồng khác độ dài (video dài hơn) -> không vá được -> dựng lại cả bản
    p.data["info"]["duration"] = 45
    p.save()
    pipeline.relayout(p, st)
    assert len(tts.read_wav(wav)[0]) == 45 * tts.SR


def test_keep_user_edits_while_relayout_runs():
    """Xếp lại chạy nền trong lúc người dùng xoá tiếp 1 câu / đổi thứ khác và đã lưu: kết quả xếp lại không đè mất sửa
    đổi đó (trước đây câu vừa xoá hiện lại), chỉ ghép giọng / vị trí của các câu còn lại."""
    from trami import pipeline
    from trami.project import Project

    p = Project.create("sửa song song")
    p.segments = [Segment(i, i + 0.8, f"句{i}", f"Câu {i}") for i in range(5)]
    p.save()
    job = Project(p.id)
    sig0 = [pipeline._seg_sig(s) for s in job.segments]
    gui = Project(p.id)
    segs = gui.segments
    del segs[2]
    gui.segments = segs
    gui.data["ghi_chu"] = "người dùng vừa sửa"
    gui.save()
    segs = job.segments
    for k, s in enumerate(segs):
        s.extra.update({"dub_at": [k, k + 0.5], "tts_file": f"f{k}.wav", "fit": "ok"})
    job.segments = segs
    job.data["dub_layout"] = [[0, 0.5, 0, "ok"]]
    pipeline._keep_user_edits(job, sig0)
    job.save()
    got = Project(p.id)
    assert [s.trans for s in got.segments] == ["Câu 0", "Câu 1", "Câu 3", "Câu 4"]
    assert got.segments[2].extra["dub_at"] == [3, 3.5] and got.segments[2].extra["tts_file"] == "f3.wav"
    assert got.data["ghi_chu"] == "người dùng vừa sửa" and got.data["dub_layout"]


def test_relayout_job_not_blocked_by_long_job(monkeypatch):
    """Xếp lại bản lồng (vài giây, không dùng card) không phải chờ việc dài của dự án khác dù đang chạy lần lượt; vẫn
    chờ việc khác của CÙNG dự án."""
    import threading
    import time as _t

    from trami import gpu, jobs as jobs_mod
    from trami.jobs import JobManager

    monkeypatch.setattr(jobs_mod, "_limit_cache", [_t.time() + 60, 1])
    monkeypatch.setattr(gpu, "before_job", lambda kind: None)
    monkeypatch.setattr(gpu, "after_job", lambda kind: None)
    monkeypatch.setattr(gpu, "wait_for_vram", lambda *a, **k: "")
    m = JobManager(workers=4)
    gate = threading.Event()
    a = m.submit("recognize", "Nghe & tách câu", lambda j: gate.wait(3), "p1")
    _t.sleep(0.2)
    b = m.submit("relayout", "Xếp lại", lambda j: None, "p2")
    c = m.submit("relayout", "Xếp lại", lambda j: None, "p1")
    _t.sleep(0.5)
    assert m.jobs[b.id].status == "done" and not m.jobs[b.id].gpu
    assert m.jobs[c.id].status == "queued" and "dự án này" in m.jobs[c.id].message
    gate.set()
    t0 = _t.time()
    while m.jobs[c.id].status != "done" and _t.time() - t0 < 5:
        _t.sleep(0.05)
    assert m.jobs[c.id].status == "done"


def test_timeline_drag_subtitle_merge_and_selection(monkeypatch):
    """Kéo khối phụ đề trên timeline: dời câu (không lấn câu bên cạnh), kéo mép đổi đầu / cuối; thả chuột -> câu đổi giờ,
    giọng lồng đi theo (xếp lại sau 1,2 s, gộp các thao tác liền nhau thành 1 việc nhẹ); xoá / gộp ở bảng Phụ đề đi chung
    đường với timeline (câu gộp được đọc lại giọng); câu chọn trên timeline tô màu nổi trong bảng."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami.gui import theme
    from trami.gui.main import MainWindow
    from trami.project import Project

    QApplication.instance() or QApplication([])
    p = Project.create("kéo phụ đề")
    segs = [Segment(2 * i, 2 * i + 1.5, f"句{i}", f"Câu {i}") for i in range(5)]
    for i, s in enumerate(segs):
        s.extra.update({"tts_file": f"tts_{i}.wav", "dub_at": [2 * i, 2 * i + 1.2]})
    p.segments = segs
    p.data["info"] = {"duration": 12}
    p.data["edit"] = {"dub": {"enabled": True, "wav": "dub.wav"}}
    p.save()
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.open_project(p.id)
    QTest.qWait(50)
    pp = win.project_page
    cv = pp.tracks.canvas
    jobs, redub = [], []
    pp.submit = lambda kind, title, fn, **k: jobs.append(kind)
    monkeypatch.setattr(pp, "_redub_rows", lambda rows: redub.append(rows))
    cv._spress = {"row": 1, "x": 0, "mode": "move", "defer": False, "orig": {1: (2.0, 3.5)}}
    assert cv._sub_drag(5.0) == {1: (2.5, 4.0)} and cv._sub_drag(-5.0) == {1: (1.5, 3.0)}   # không lấn câu bên cạnh
    cv._spress["mode"] = "l"
    assert cv._sub_drag(-3.0) == {1: (1.5, 3.5)} and cv._sub_drag(3.0)[1][0] == pytest.approx(3.3)
    cv._spress = None
    # kéo thật bằng chuột: khối câu 2 (4,0–5,5 s) sang phải 0,3 s
    pp.tracks.fit()
    y = int(cv.track_rect("subs").center().y())
    x0 = int(cv.x(4.75))
    dx = int(round(0.3 * pp.tracks.pps))
    QTest.mousePress(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0, y))
    QTest.mouseMove(cv, QPoint(x0 + dx // 2, y))
    QTest.mouseMove(cv, QPoint(x0 + dx, y))
    QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x0 + dx, y))
    s2 = pp.model.segs[2]
    assert s2.start == pytest.approx(4.3, abs=0.05) and s2.end - s2.start == pytest.approx(1.5, abs=0.01)
    assert Project(p.id).segments[2].start == pytest.approx(s2.start)
    assert pp._relayout_go.isActive() and not jobs
    # xoá 1 câu ở bảng Phụ đề trong lúc chờ -> gộp vào cùng 1 lần xếp lại
    pp._select_line(4)
    pp._seg_op("delete")
    assert len(pp.model.segs) == 4 and pp._relayout_deleted == 1
    pp._relayout_go.stop()
    pp._relayout_now()
    assert jobs == ["relayout"]
    # gộp câu đã có giọng -> đọc lại giọng câu gộp
    pp._sub_op("merge", [0])
    assert len(pp.model.segs) == 3 and pp.model.segs[0].trans == "Câu 0 Câu 1" and redub == [[0]]
    # câu chọn trên timeline: bảng tô màu nổi
    pp._select_line(2)
    assert pp.dub_table.selectionModel().isRowSelected(pp.dub_proxy.mapFromSource(pp.model.index(2, 0)).row())
    assert "QTableView::item:selected" in theme.stylesheet()
    win.close()


def test_stretch_cover_hides_text(tmp_path):
    """Che phụ đề kiểu Kéo dọc: vùng chữ được phủ bằng dòng điểm ảnh sát mép (trên / dưới / 2 đầu) – không còn chữ, cả
    Xem thử 1 khung (numpy) lẫn khi xuất (FFmpeg)."""
    import numpy as np

    from trami import ffmpeg as ff, video

    img = np.zeros((180, 320, 3), np.uint8)
    for y in range(180):
        img[y] = (y, 100, 200 - y)
    img[140:160, 40:280] = 255                                       # "chữ" trắng
    for side in ("both", "above", "below"):
        out = video.stretch_fill(img, 40, 136, 280, 164, side, smooth=0)
        assert out.shape == (28, 240, 3) and out.max() < 180
    assert video.stretch_rows(180, 170, 10, "below") == (168, None)   # sát mép dưới -> dùng dải phía trên
    src = tmp_path / "v.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x305070:s=320x180:r=25:d=1,drawbox=x=40:y=140:w=240:h=20:c=white:t=fill",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    spec = {"hardsub": {"mode": "stretch", "regions": [{"x": 0.1, "y": 0.75, "w": 0.8, "h": 0.17}]}}
    video.build_and_render(src, tmp_path / "o.mp4", spec, {"video_preset": "ultrafast"}, tmp_path / "w", [])
    raw = subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-i", str(tmp_path / "o.mp4"), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"], capture_output=True).stdout
    g = np.frombuffer(raw, np.uint8).reshape(180, 320)
    assert g[140:160, 40:280].max() < 150


def test_chunked_export_matches_and_reuses(tmp_path, monkeypatch):
    """Xuất theo từng đoạn: cùng số khung / hình như xuất 1 lần (có cắt giữa, mờ dần, làm mờ vùng, phụ đề); sửa 1 câu
    rồi xuất lại -> chỉ mã hoá lại đoạn có câu đó, dùng lại âm thanh."""
    import numpy as np

    from trami import ffmpeg as ff, video

    src = tmp_path / "src.mp4"
    subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:d=8", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    monkeypatch.setattr(video, "CHUNK_MIN_DUR", 3.0)
    monkeypatch.setattr(video, "CHUNK_SEC", 2.0)
    segs = [Segment(i + 0.1, i + 0.8, "中", f"Câu {i}") for i in range(8)]
    spec = {"cuts": [[3.0, 4.0]], "fade": {"in": 0.5, "out": 0.5},
            "hardsub": {"mode": "blur", "regions": [{"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.12}]},
            "subtitles": {"burn": True, "which": "trans"}}
    st = {"video_preset": "ultrafast"}
    calls = []
    real = ff.run
    monkeypatch.setattr(ff, "run", lambda args, *a, **k: (calls.append(str(args[-1])), real(args, *a, **k))[1])
    video.build_and_render(src, tmp_path / "one.mp4", spec, dict(st, export_chunks=False), tmp_path / "w1", segs)
    video.build_and_render(src, tmp_path / "ch.mp4", spec, st, tmp_path / "w2", segs)
    assert sum(".part.mp4" in c for c in calls) == 4

    def frames(p):
        raw = subprocess.run([ff.ffmpeg_bin(), "-loglevel", "error", "-i", str(p), "-f", "rawvideo", "-pix_fmt", "gray",
                              "pipe:1"], capture_output=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(-1, 180, 320).astype(float)

    a, b = frames(tmp_path / "one.mp4"), frames(tmp_path / "ch.mp4")
    assert len(a) == len(b) == 175
    assert np.abs(a - b).mean(axis=(1, 2)).max() < 2.0
    assert ff.probe(tmp_path / "ch.mp4").has_audio
    calls.clear()
    segs[6].trans = "Câu đã sửa"                                    # giây 6 gốc = giây 5 xuất -> đoạn 3
    video.build_and_render(src, tmp_path / "ch2.mp4", spec, st, tmp_path / "w2", segs)
    assert sum(".part.mp4" in c for c in calls) == 1 and not any(".part.m4a" in c for c in calls)
    assert len(list((tmp_path / "w2" / "chunks").glob("c_*.mp4"))) == 4


def test_voice_check_flags_music_lines():
    """Lọc câu nghe nhầm: câu là nốt nhạc đứng yên / tiếng ồn / tiếng rất nhỏ bị đánh dấu, lời thoại thật thì không."""
    import numpy as np

    from trami import voicecheck

    sr, rng = 16000, np.random.default_rng(0)
    parts, segs, t = [], [], 0.0

    def speech(dur, base, gain=0.3):
        n = int(dur * sr)
        tt = np.arange(n) / sr
        f = base * (1 + 0.12 * np.sin(2 * np.pi * 1.7 * tt + rng.uniform(0, 6)) + 0.05 * np.sin(2 * np.pi * 5 * tt))
        ph = 2 * np.pi * np.cumsum(f) / sr
        x = sum(np.sin(k * ph) / k for k in range(1, 6))
        env = (np.sin(2 * np.pi * 4 * tt) > -0.3).astype(float)      # âm tiết ~4 / s
        return gain * x * env

    def add(x, text):
        nonlocal t
        parts.append(x)
        segs.append(Segment(t, t + len(x) / sr, text, ""))
        parts.append(np.zeros(int(0.3 * sr)))
        t += len(x) / sr + 0.3

    for i in range(10):
        add(speech(1.6, 190 + 8 * (i % 4)), "我们今天去哪里吃")
    tt = np.arange(int(1.6 * sr)) / sr
    add(0.3 * np.sin(2 * np.pi * 660 * tt), "啊啊啊啊")                     # nốt nhạc cao, đứng yên
    add(0.3 * rng.standard_normal(int(1.6 * sr)), "什么东西")                # tiếng ồn
    add(speech(1.6, 200, gain=0.002), "你好你好")                           # rất nhỏ (nhạc lọt vào)
    audio = np.concatenate(parts).astype(np.float32)
    r = voicecheck.check_lines(segs, audio, trim=False)
    assert r["suspect"] == [10, 11, 12]
    assert all("noise" in segs[k].extra for k in (10, 11, 12)) and not any("noise" in s.extra for s in segs[:10])


def test_length_marker_fill_range():
    """Dịch vừa thời lượng: câu đủ dài gửi kèm khoảng ⟪tối thiểu–tối đa⟫ để AI thêm từ đệm, câu rất ngắn chỉ có tối đa."""
    from trami import translate

    assert translate.length_marker(1.0, "vi", "tiếng") == "⟪≤5 tiếng⟫"
    assert translate.length_marker(3.0, "vi", "tiếng") == "⟪9–15 tiếng⟫"
    assert translate.length_marker(3.0, "vi", "tiếng", fill=False) == "⟪≤15 tiếng⟫"


def test_wait_for_vram(monkeypatch):
    """Card bị chương trình khác chiếm: báo tên chương trình + chờ, quá hạn thì chạy kèm ghi chú; đủ chỗ thì chạy ngay."""
    from trami import gpu
    from trami.ffmpeg import Cancelled

    free = {"mb": 900}

    def smi(args):
        if args[0].startswith("--query-gpu"):
            return [f"{free['mb']}, 8188"]
        return ["4321, C:\\Program Files\\Chrome\\chrome.exe, 3500"]

    monkeypatch.setattr(gpu, "_smi", smi)
    st = {"asr_engine": "qwen3", "qwen_asr_model": "Qwen/Qwen3-ASR-1.7B"}
    msgs = []
    note = gpu.wait_for_vram("recognize", msgs.append, None, st, wait_max=0.3, poll=0.1)
    assert "card chỉ còn trống" in note and msgs and "chrome.exe 3.4 GB" in msgs[0]
    with pytest.raises(Cancelled):
        gpu.wait_for_vram("recognize", None, lambda: True, st, wait_max=5, poll=0.5)
    free["mb"] = 7000
    assert gpu.wait_for_vram("recognize", None, None, st) == ""
    assert gpu.wait_for_vram("translate", None, None, dict(st, translate_engine="google")) == ""
    assert gpu.wait_for_vram("recognize", None, None, dict(st, vram_check=False)) == ""


def test_undo_redo_subtitle_ops(monkeypatch):
    """Hoàn tác / làm lại: xoá rồi gộp = 2 bước riêng; gõ chữ liền nhau gộp 1 bước; hoàn tác ghi đĩa + xếp lại giọng."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    from trami.gui.main import MainWindow
    from trami.gui.subs_model import C_VI
    from trami.project import Project

    QApplication.instance() or QApplication([])
    p = Project.create("hoàn tác")
    segs = [Segment(2 * i, 2 * i + 1.5, f"句{i}", f"Câu {i}") for i in range(5)]
    for i, s in enumerate(segs):
        s.extra.update({"tts_file": f"tts_{i}.wav", "dub_at": [2 * i, 2 * i + 1.2]})
    p.segments = segs
    p.data["info"] = {"duration": 12}
    p.data["edit"] = {"dub": {"enabled": True, "wav": "dub.wav"}}
    p.save()
    win = MainWindow()
    win.show()
    win.open_project(p.id)
    QTest.qWait(50)
    pp = win.project_page
    relayouts = []
    monkeypatch.setattr(pp, "_relayout", lambda *a, **k: relayouts.append(a))
    monkeypatch.setattr(pp, "_redub_rows", lambda rows: None)
    assert not pp.undo_btn.isEnabled()
    pp._sub_op("delete", [4])
    pp._sub_op("merge", [0])
    assert [s.trans for s in pp.model.segs] == ["Câu 0 Câu 1", "Câu 2", "Câu 3"]
    pp.undo()
    assert len(pp.model.segs) == 4 and pp.model.segs[0].trans == "Câu 0"
    assert len(Project(p.id).segments) == 4 and relayouts
    pp.undo()
    assert len(pp.model.segs) == 5 and not pp.undo_btn.isEnabled()
    pp.redo()
    assert len(pp.model.segs) == 4
    for txt in ("A", "AB", "ABC"):                                       # gõ liền nhau -> 1 bước
        pp.model.setData(pp.model.index(1, C_VI), txt)
    assert pp.model.segs[1].trans == "ABC"
    pp.undo()
    assert pp.model.segs[1].trans == "Câu 1" and pp.redo_btn.isEnabled()
    win.close()


def test_ai_song_filter_before_translate(monkeypatch):
    """AI lọc lời bài hát / chữ không phải thoại theo NỘI DUNG trước khi dịch cả phim lần đầu: lời hát -> 🎵 (không lồng
    tiếng), câu máy tự bịa -> bỏ, đoạn lời hát dính vào câu thoại -> chỉ xoá phần đó (không cho sửa chữ khác); kiểm tra
    bản dịch nhận khoảng độ dài M–N."""
    from trami import pipeline, skills
    from trami import translate as tr
    from trami.project import Project

    calls = []

    def fake_chat(self, system, user, max_tokens=8000, temperature=0.3):
        kind = ("filter" if "CHỈ làm 1 việc" in system else "review" if "kiểm tra bản dịch" in system
                else "profile" if 'lập "HỒ SƠ PHIM"' in system else "translate")
        calls.append((kind, user))
        if kind == "filter":
            return json.dumps({"song": {"3": "văn vần, điệp khúc"}, "noise": {"5": "máy tự bịa"},
                               "fix": {"2": "你去哪儿", "4": "完全不同的一句话"}}, ensure_ascii=False)
        if kind == "profile":
            return "{}"
        payload = json.loads(user[user.index("{"):])
        return json.dumps({k: f"Câu {k}" for k in payload} if kind == "translate" else {}, ensure_ascii=False)

    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    st_ = {"translate_engine": "openai", "openai_api_key": "x", "openai_base_url": "https://api.example.com/v1",
           "ai_clean_before_translate": False, "ai_review_after_translate": True}
    p = Project.create("loc_loi_hat")
    p.segments = [Segment(0, 1, "你好"), Segment(1.2, 3.5, "你去哪儿我爱你到永远"), Segment(4, 8, "我爱你到永远"),
                  Segment(9, 10, "走吧"), Segment(11, 13, "请不吝点赞订阅转发")]
    p.save()
    pipeline.translate(p, "openai", "auto", False, st_)
    segs = p.segments
    assert [s.text for s in segs] == ["你好", "你去哪儿", "我爱你到永远", "走吧"]
    assert segs[2].extra["song"] and segs[2].extra["ai_song"] == "văn vần, điệp khúc"
    assert segs[1].extra["src_before"] == "你去哪儿我爱你到永远" and "src_before" not in segs[3].extra
    assert "lời bài hát" in p.data["skill_note"] and "bỏ 1 câu" in p.data["skill_note"]
    review = [u for k, u in calls if k == "review"][0]
    assert "≤5 tiếng" in review and "13–20 tiếng" in review               # 1 s -> tối đa; 4 s -> khoảng M–N
    calls.clear()
    pipeline.translate(p, "openai", "auto", False, st_)                     # đã lọc rồi -> không lọc lại
    assert "filter" not in [k for k, _ in calls]
    assert pipeline.undo_cleanup(p, st_) == 5
    assert skills._is_trim("你去哪儿我爱你", "你去哪儿") and not skills._is_trim("走吧", "跑吧")


def test_false_stop_rules():
    """Dấu "。" giả chỉ nhận khi có bằng chứng ngữ pháp chắc chắn; câu đã trọn thì giữ (lấy từ phim người dùng gửi)."""
    from trami import align

    assert align.false_stop("爹，嗯，我刚回来就。", "收到延旭将军传讯，他说什么？") == "tail"
    assert align.false_stop("就连今日的风。", "都比前些日子轻了几分，") == "opener"
    assert align.false_stop("既然仙缘。", "咱们已经反抽过一次，") == "opener"
    assert align.false_stop("你看他说。", "了吧，我就知道") == "head"
    for a, b in [("本君得在。", "你不说，老夫也会安排。"), ("你还欠我一个玉钵，记得还。", "还有老夫的守孝玉恶阵。"),
                 ("一百遍。", "娘，俺骂的可是天界。"), ("…前面所有人全白忙，这笔账。", "你自己算。"),
                 ("如果你去我就去。", "好吗"), ("他还在找，我还在记。", "够了，能不能别排比了？")]:
        assert align.false_stop(a, b) == "", a
    assert align.broken_junction("勤俭持家，会", "过日子。")      # không dấu câu, dừng ở động từ năng nguyện


def test_join_broken_fixes_false_stops_and_shifts_tail():
    """Nối câu bị chặt bởi "。" giả / dòng dừng ở 会; nối thì quá dài -> chuyển mẩu đuôi sang câu sau; câu đúng giữ nguyên."""
    from trami import align

    rows = [(52.97, 54.19, "俺这是勤俭持家。"), (55.13, 55.81, "勤俭持家，会"), (58.09, 58.19, "过日子。"),
            (437.03, 439.45, "他还在写，他还在考，"), (440.47, 443.05, "他还在找，我还在记。"),
            (443.83, 446.01, "够了，能不能别排比了？"), (632.89, 633.48, "一百遍。"), (633.5, 636.69, "娘，俺骂的可是天界。"),
            (1042.22, 1043.36, "就连今日的风。"), (1044.48, 1046.26, "都比前些日子轻了几分，"),
            (1047.2, 1051.86, "这点重建损失跟整个凡界重新活过来相比，算什么？"),
            (2071.86, 2074.878, "爹，嗯，我刚回来就。"), (2074.898, 2077.33, "收到延旭将军传讯，他说什么？"),
            (3247.57, 3248.79, "既然仙缘。"), (3250.37, 3252.31, "咱们已经反抽过一次，"), (3254.37, 3256.47, "那就别只抽一次。"),
            (3509.31, 3510.37, "本君得在。"), (3511.23, 3512.61, "你不说，老夫也会安排。"),
            (3710.25, 3715.47, "为俺们天剑宗变穷的账，为大师兄挨的雷，为三师兄"),
            (3717.45, 3719.96, "让雷劈焦的辫子，为四师兄劈没的围裙，")]
    segs = [Segment(a, b, t) for a, b, t in rows]
    out, made = align.join_broken(segs, align.split_params({}), 28, 4000)
    texts = [s.text for s in out]
    assert "勤俭持家，会过日子。" in texts
    assert "就连今日的风，都比前些日子轻了几分，" in texts
    assert "爹，嗯，我刚回来就收到延旭将军传讯，他说什么？" in texts         # nói liền 0,02 s -> bỏ hẳn "。"
    assert "既然仙缘，咱们已经反抽过一次，" in texts and "那就别只抽一次。" in texts
    assert "为俺们天剑宗变穷的账，为大师兄挨的雷，" in texts
    assert "为三师兄让雷劈焦的辫子，为四师兄劈没的围裙，" in texts
    for t in ("俺这是勤俭持家。", "他还在写，他还在考，", "他还在找，我还在记。", "够了，能不能别排比了？", "一百遍。",
              "娘，俺骂的可是天界。", "本君得在。", "你不说，老夫也会安排。", "这点重建损失跟整个凡界重新活过来相比，算什么？"):
        assert t in texts, t
    assert len(out) == len(segs) - 4 and made == 5
    shifted = [s for s in out if s.text.startswith("为三师兄")][0]
    assert shifted.start == pytest.approx(3710.25 + 5.22 * 17 / 21, abs=0.01)


def test_heard_junction_relisten():
    """Nghe lại chỗ cắt khúc có ngữ cảnh: không có dấu kết câu ở chỗ nối -> nối; có -> giữ; sửa chữ nghe sai sát chỗ cắt."""
    from trami import align

    r = align.heard_junction("爹，嗯，我刚回来就。", "收到延旭将军传讯，他说什么？", "嗯，我刚回来就收到延旭将军传讯，他说")
    assert r and not r["end"] and r["mark"] == "" and not r["fixed"]
    r = align.heard_junction("这样的资质，三界难。", "找第二个，阿弥陀佛！", "根基稳，这样的资质，三界难找第二个，阿弥陀佛。")
    assert r and not r["end"]
    r = align.heard_junction("一百遍。", "娘，俺骂的可是天界。", "严行戒律，一百遍。娘，俺骂的可是天界。")
    assert r and r["end"]
    r = align.heard_junction("天界本源，确实得天独厚", "所以既然仙缘", "确实得天独厚，所以既然仙缘")
    assert r and not r["end"] and r["mark"] == "，"
    r = align.heard_junction("爹，我刚回来九。", "收到延旭将军传讯", "我刚回来就收到延旭将军传讯")
    assert r["fixed"] == 1 and r["t1"] == "爹，我刚回来就。" and r["t2"] == "收到延旭将军传讯"
    assert align.heard_junction("爹，我刚回来就。", "收到延旭将军传讯", "今天天气很好我们出去玩") is None


def test_relisten_cuts_merges_false_stop(monkeypatch):
    """Chỗ cắt khúc không có khoảng nghỉ + khúc trước kết thúc bằng "。" -> nghe lại 12 s quanh đó; câu bị chặt đôi được
    nối (bước tách câu sau đó tách lại), lượt nói nối nhau thì giữ; khúc nghỉ thật không nghe lại."""
    from trami import asr_qwen

    calls = []

    class FakeWorker:
        def call(self, req, progress=None, cancel=None):
            calls.append(req)
            heard = {0: "嗯，我刚回来就收到延旭将军传讯，他说", 1: "严行戒律，一百遍。娘，俺骂的可是天界"}
            return {"items": [{"text": heard[k]} for k in range(len(req["spans"]))]}

    monkeypatch.setattr(asr_qwen, "worker", lambda settings=None: FakeWorker())
    raw = [(0.0, 28.0, "魔界的事，爹，嗯，我刚回来就。"), (28.02, 40.0, "收到延旭将军传讯，他说什么？"),
           (41.5, 52.0, "回去，万仙盟公共场合严行戒律，一百遍。"), (52.02, 56.0, "娘，俺骂的可是天界。"),
           (57.0, 60.0, "好。")]
    st = {}
    out = asr_qwen.relisten_cuts(raw, "x.wav", {}, "zh", stats=st)
    assert [t for _, _, t in out] == ["魔界的事，爹，嗯，我刚回来就收到延旭将军传讯，他说什么？",
                                      "回去，万仙盟公共场合严行戒律，一百遍。", "娘，俺骂的可是天界。", "好。"]
    assert out[0][:2] == (0.0, 40.0) and st == {"cuts": 2, "merged": 1, "fixed": 0}
    assert calls[0]["spans"] == [[22.0, pytest.approx(34.02)], [46.0, 56.0]] and calls[0]["align"] is False


def test_ai_join_mode_only_merges(monkeypatch):
    """AI nối câu (mục 4): chỉ gộp 2–3 dòng liền nhau, chữ giữ nguyên (AI viết khác chữ -> tự bỏ "。" ở chỗ nối), quá
    dài / quá 3 dòng / khác người nói -> bỏ qua; dòng không gộp giữ nguyên đối tượng; AI nhận khoảng nghỉ giữa các dòng."""
    from trami import pipeline, skills

    seen = []

    class FakeLLM:
        def chat(self, system, user, max_tokens=6000, temperature=0.1):
            seen.append((system, user))
            return json.dumps({"merge": [[1, 2], [3, 4], [5, 6, 7, 8], [9, 10]],
                               "fix": {"1": "前面所有人全白忙，这笔账你自己算。", "3": "三界难找第二个人啊"}},
                              ensure_ascii=False)

    segs = [Segment(0, 7.7, "前面所有人全白忙，这笔账。"), Segment(8.07, 9.05, "你自己算。"),
            Segment(20, 22.1, "这样的资质，三界难。"), Segment(22.12, 24.9, "找第二个，阿弥陀佛！"),
            Segment(30, 31, "一。"), Segment(31.1, 32, "二。"), Segment(32.1, 33, "三。"), Segment(33.1, 34, "四。"),
            Segment(40, 41, "好。", speaker="S1"), Segment(41.2, 42, "走吧。", speaker="S2"),
            Segment(50, 52, "阿弥陀佛。")]
    res = skills.clean_lines(FakeLLM(), segs, mode="join")
    assert "nói liền" in seen[0][1] and "nghỉ 0.4s" in seen[0][1] and "(S2)" in seen[0][1]
    assert "CHỈ làm" in seen[0][0]
    out, st = skills.apply_cleanup(segs, res)
    texts = [s.text for s in out]
    assert texts[0] == "前面所有人全白忙，这笔账你自己算。" and (out[0].start, out[0].end) == (0, 9.05)
    assert texts[1] == "这样的资质，三界难找第二个，阿弥陀佛！"     # AI viết khác chữ -> tự bỏ "。" giả (nói liền)
    assert texts[2:] == ["一。", "二。", "三。", "四。", "好。", "走吧。", "阿弥陀佛。"]
    assert out[-1] is segs[-1] and st["merged"] == 2 and st["changed"] == [0, 1]
    # chọn AI cho mục 4 trong danh sách AI ở Cài đặt
    assert pipeline.join_engine({"translate_engine": "google"})[0] is None
    assert pipeline.join_engine({"translate_engine": "gemini", "gemini_api_keys": ["k"]}) == ("gemini", "Gemini")
    assert pipeline.join_engine({"ai_join_engine": "deepseek"})[0] is None
    assert pipeline.join_engine({"ai_join_engine": "ollama"}) == ("ollama", "Ollama")
    assert pipeline.join_engine({"ai_join_engine": "off", "translate_engine": "gemini",
                                 "gemini_api_keys": ["k"]})[0] is None
    ch = dict(pipeline.join_engine_choices({"anthropic_api_keys": ["k"]}))
    assert ch["anthropic"] == "Claude" and ch["deepseek"].endswith("chưa có API key") and "off" in ch


def test_ai_join_project_and_undo(monkeypatch):
    """Nút "AI nối câu": gộp theo AI đã chọn ở Cài đặt, câu gộp bỏ giọng cũ, "Hoàn tác nối" về bản cũ; chưa có key -> báo."""
    from trami import pipeline
    from trami import translate as tr
    from trami.project import Project

    used = []

    def fake_chat(self, system, user, max_tokens=6000, temperature=0.1):
        used.append(self.engine)
        return json.dumps({"merge": [[1, 2]], "fix": {}})

    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    p = Project.create("ai_noi_cau")
    p.segments = [Segment(0, 7.7, "前面所有人全白忙，这笔账。", "Cả đám uổng công, món nợ này."),
                  Segment(8.07, 9.05, "你自己算。", "Cậu tự tính."), Segment(12, 13, "好。")]
    p.segments[0].extra["tts_file"] = "a.wav"
    p.save()
    st = {"ai_join_engine": "deepseek", "deepseek_api_keys": ["k"]}
    assert pipeline.ai_join(p, st) == [0] and used == ["deepseek"]
    segs = p.segments
    assert [s.text for s in segs] == ["前面所有人全白忙，这笔账，你自己算。", "好。"]
    assert "tts_file" not in segs[0].extra
    assert pipeline.undo_join(p, st) == 3 and p.segments[1].text == "你自己算。"
    with pytest.raises(ValueError, match="chưa có API key"):
        pipeline.ai_join(p, {"ai_join_engine": "gemini"})


# ------------------------------------------------------------------ đăng video (máy chủ giả YouTube / Facebook / Zernio)
class _FakeSocial:
    """Máy chủ giả: Google OAuth + YouTube tải lên nối tiếp (rớt 1 lần giữa chừng), Facebook (phiên tải lên nối tiếp,
    Reels), Zernio (hồ sơ, tài khoản, chỗ tải lên ký sẵn, bài đăng)."""

    def __init__(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse

        st = self.st = {"yt": b"", "yt_failed": False, "fb": b"", "fb_failed": False, "reel": b"", "zn": b"",
                        "calls": [], "yt_meta": None, "fb_post": None, "reel_finish": None, "zn_post": None,
                        "tokens": []}
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, obj=None, headers=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _route(self, method):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                path = u.path
                st["calls"].append((method, path))
                body = self._body()
                form = {k: v[0] for k, v in parse_qs(body.decode("latin-1")).items()} \
                    if self.headers.get("Content-Type", "").startswith("application/x-www-form") else {}
                b = outer.base
                # ---------------- Google / YouTube
                if path == "/g/token":
                    st["tokens"].append(form.get("grant_type"))
                    if form.get("grant_type") == "authorization_code":
                        assert form.get("code") == "GCODE" and form.get("code_verifier")
                        return self._send(200, {"access_token": "AT1", "expires_in": 3600, "refresh_token": "RT1"})
                    return self._send(200, {"access_token": "AT2", "expires_in": 3600})
                if path == "/yt/api/channels":
                    return self._send(200, {"items": [{"id": "UC1", "snippet": {"title": "Kênh Thử"}}]})
                if path == "/yt/upload/videos":
                    assert self.headers["Authorization"] == "Bearer AT2"      # token hết hạn đã được làm mới
                    st["yt_meta"] = json.loads(body)
                    st["yt_total"] = int(self.headers["X-Upload-Content-Length"])
                    return self._send(200, {}, {"Location": b + "/yt/session/1"})
                if path == "/yt/session/1":
                    total = st["yt_total"]
                    rng = self.headers.get("Content-Range", "")
                    have = len(st["yt"])
                    if rng.startswith("bytes */"):
                        return self._send(308, None, {"Range": f"bytes=0-{have - 1}"} if have else {})
                    a = int(rng.split(" ")[1].split("-")[0])
                    if a > 0 and not st["yt_failed"]:
                        st["yt_failed"] = True                                  # rớt giữa chừng 1 lần
                        return self._send(503, {"error": {"message": "backend"}})
                    assert a == have
                    st["yt"] += body
                    if len(st["yt"]) >= total:
                        return self._send(200, {"id": "VID1", "status": {"privacyStatus": "private"}})
                    return self._send(308, None, {"Range": f"bytes=0-{len(st['yt']) - 1}"})
                if path == "/yt/upload/thumbnails/set":
                    return self._send(403, {"error": {"message": "The user doesn't have permissions"}})
                # ---------------- Facebook
                if path == "/fb/v23.0/oauth/access_token":
                    return self._send(200, {"access_token": "LONG" if q.get("fb_exchange_token") else "SHORT"})
                if path == "/fb/v23.0/me":
                    return self._send(200, {"id": "U1", "name": "Đào"})
                if path == "/fb/v23.0/me/accounts":
                    assert q.get("access_token") == "LONG"
                    return self._send(200, {"data": [{"id": "P1", "name": "Trang 1", "access_token": "PT1"}],
                                            "paging": {"next": b + "/fb/v23.0/me/accounts2?access_token=LONG"}})
                if path == "/fb/v23.0/me/accounts2":
                    return self._send(200, {"data": [{"id": "P2", "name": "Trang 2", "access_token": "PT2"}]})
                if path == "/fb/v23.0/APP1/uploads":
                    assert q["access_token"] == "LONG" and int(q["file_length"]) > 0
                    st["fb_total"] = int(q["file_length"])
                    return self._send(200, {"id": "upload:S1"})
                if path == "/fb/v23.0/upload:S1":
                    assert self.headers["Authorization"] == "OAuth LONG"
                    if method == "GET":
                        return self._send(200, {"id": "upload:S1", "file_offset": len(st["fb"])})
                    off = int(self.headers["file_offset"])
                    assert off == len(st["fb"])
                    if not st["fb_failed"]:
                        st["fb_failed"] = True
                        st["fb"] += body[: len(body) // 2]                        # nhận được 1 nửa rồi rớt
                        return self._send(503, {"error": {"message": "temporary"}})
                    st["fb"] += body
                    return self._send(200, {"h": "H1"})
                if path == "/fb/v23.0/P1/videos":
                    st["fb_post"] = body
                    return self._send(200, {"id": "FV1"})
                if path == "/fb/v23.0/P1/video_reels":
                    if q.get("upload_phase") == "start":
                        return self._send(200, {"video_id": "R1", "upload_url": b + "/rup/R1"})
                    st["reel_finish"] = q
                    return self._send(200, {"success": True})
                if path == "/rup/R1":
                    assert self.headers["Authorization"] == "OAuth PT1" and self.headers["offset"] == "0"
                    st["reel"] += body
                    return self._send(200, {"success": True})
                # ---------------- Zernio
                if path.startswith("/zn/"):
                    assert self.headers["Authorization"] == "Bearer ZKEY"
                if path == "/zn/profiles":
                    return self._send(200, {"profiles": []} if method == "GET" else {"profile": {"_id": "PR1"}})
                if path == "/zn/connect/youtube":
                    assert q["profileId"] == "PR1"
                    return self._send(200, {"authUrl": "https://zernio.example/connect/yt"})
                if path == "/zn/accounts":
                    return self._send(200, {"accounts": [
                        {"_id": "A1", "platform": "youtube", "displayName": "Kênh Z"},
                        {"_id": "A2", "platform": "facebook", "name": "Trang Z"},
                        {"_id": "A3", "platform": "tiktok", "name": "TT"}]})
                if path == "/zn/media/presign":
                    return self._send(200, {"uploadUrl": b + "/up/1", "publicUrl": "https://media.zernio.com/v.mp4"})
                if path == "/up/1":
                    st["zn"] += body
                    return self._send(200, {})
                if path == "/zn/posts":
                    st["zn_post"] = json.loads(body)
                    return self._send(200, {"post": {"_id": "PO1", "status": "publishing",
                                                     "platforms": [{"platform": "youtube", "status": "publishing"}]}})
                if path == "/zn/posts/PO1":
                    return self._send(200, {"post": {"_id": "PO1", "status": "published", "platforms": [
                        {"platform": "youtube", "status": "published",
                         "platformPostUrl": "https://www.youtube.com/watch?v=ZZ"}]}})
                return self._send(404, {"error": {"message": "no route " + path}})

            def do_GET(self):  # noqa: N802
                self._route("GET")

            def do_POST(self):  # noqa: N802
                self._route("POST")

            def do_PUT(self):  # noqa: N802
                self._route("PUT")

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def patch(self, monkeypatch):
        from trami import publish

        b = self.base
        monkeypatch.setattr(publish, "GOOGLE_TOKEN", b + "/g/token")
        monkeypatch.setattr(publish, "GOOGLE_AUTH", b + "/g/auth")
        monkeypatch.setattr(publish, "YT_API", b + "/yt/api")
        monkeypatch.setattr(publish, "YT_UPLOAD", b + "/yt/upload")
        monkeypatch.setattr(publish, "FB_GRAPH", b + "/fb")
        monkeypatch.setattr(publish, "ZERNIO_API", b + "/zn")
        monkeypatch.setattr(publish, "CHUNK", 256 * 1024)
        monkeypatch.setattr(publish.net, "sleep", lambda *a, **k: None)
        monkeypatch.setattr(publish, "check_video", lambda *a: ("", ""))

    def close(self):
        self.srv.shutdown()


def _browser(answer):
    """Giả trình duyệt: đọc địa chỉ đăng nhập, gửi mã về cổng trên máy (redirect_uri) như Google / Facebook làm."""
    import threading
    import urllib.request
    from urllib.parse import parse_qs, urlencode, urlparse

    def opener(url):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        dest = q["redirect_uri"] + "?" + urlencode(dict(answer, state=q["state"]))
        threading.Thread(target=lambda: urllib.request.urlopen(dest, timeout=10).read(), daemon=True).start()
        opener.query = q
    return opener


def test_publish_youtube_api_login_and_resumable_upload(monkeypatch, tmp_path):
    from trami import publish
    from trami.project import Project

    fake = _FakeSocial()
    fake.patch(monkeypatch)
    try:
        s = {"yt_client_id": "CID", "yt_client_secret": "SEC"}
        br = _browser({"code": "GCODE"})
        acc = publish.yt_login(s, opener=br)
        assert br.query["redirect_uri"].startswith("http://127.0.0.1:") and br.query["code_challenge_method"] == "S256"
        assert acc["id"] == "UC1" and acc["name"] == "Kênh Thử" and acc["refresh_token"] == "RT1"
        acc["expires"] = 0                                     # mã truy cập hết hạn -> phải làm mới trước khi tải
        publish._upsert("youtube", acc)
        [t] = [x for x in publish.targets("youtube") if x["via"] == "api"]
        assert t["id"] == "yt:UC1" and publish.targets("youtube")[-1]["via"] == "manual"
        vid = tmp_path / "phim.mp4"
        data = os.urandom(700 * 1024)
        vid.write_bytes(data)
        thumb = tmp_path / "bia.jpg"
        thumb.write_bytes(b"\xff\xd8jpeg")
        p = Project.create("dang")
        marks = []
        rec = publish.publish(p, s, {"platform": "youtube", "kind": "short", "target": t, "file": str(vid),
                                     "title": "Phim <hay> nhất", "description": "Mô tả", "hashtags": "#phim, hoathinh",
                                     "privacy": "public", "thumbnail": str(thumb)},
                              lambda f, m: marks.append(f))
        assert fake.st["yt"] == data                           # đủ từng byte dù rớt 1 lần giữa chừng
        assert fake.st["yt_failed"] and "refresh_token" in fake.st["tokens"]
        meta = fake.st["yt_meta"]
        assert meta["snippet"]["title"] == "Phim hay nhất"     # YouTube không nhận < >
        assert meta["snippet"]["description"].endswith("#Shorts #phim #hoathinh")
        assert meta["snippet"]["tags"] == ["phim", "hoathinh"] and meta["status"]["privacyStatus"] == "public"
        assert rec["url"] == "https://www.youtube.com/shorts/VID1" and rec["status"] == "published"
        assert "khoá Riêng tư" in rec["warn"] and "ảnh bìa" in rec["warn"]
        assert publish.load_posts(p)[-1]["remote_id"] == "VID1" and max(marks) > 0.9
        # hẹn giờ: Riêng tư + publishAt (UTC)
        fake.st.update(yt=b"", yt_failed=True)
        from datetime import datetime, timedelta

        when = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
        rec = publish.publish(p, s, {"platform": "youtube", "kind": "video", "target": t, "file": str(vid),
                                     "title": "A", "publish_at": when.isoformat()})
        st_ = fake.st["yt_meta"]["status"]
        assert st_["privacyStatus"] == "private" and st_["publishAt"].endswith("Z") and rec["status"] == "scheduled"
    finally:
        fake.close()


def test_publish_facebook_pages_video_resume_and_reel(monkeypatch, tmp_path):
    from trami import publish
    from trami.project import Project

    fake = _FakeSocial()
    fake.patch(monkeypatch)
    try:
        s = {"fb_app_id": "APP1", "fb_app_secret": "S"}
        pages = publish.fb_login(s, opener=_browser({"code": "FCODE"}))
        assert [x["id"] for x in pages] == ["P1", "P2"] and pages[0]["user_token"] == "LONG"
        t1 = next(x for x in publish.targets("facebook") if x["id"] == "fb:P1")
        vid = tmp_path / "phim.mp4"
        data = os.urandom(300 * 1024)
        vid.write_bytes(data)
        p = Project.create("fb")
        rec = publish.publish(p, s, {"platform": "facebook", "kind": "video", "target": t1, "file": str(vid),
                                     "title": "Tập 1", "description": "Hay lắm", "hashtags": ["phim"]})
        assert fake.st["fb"] == data and fake.st["fb_failed"]  # gửi tiếp từ byte Facebook đã nhận
        assert b"fbuploader_video_file_chunk=H1" in fake.st["fb_post"] and b"access_token=PT1" in fake.st["fb_post"]
        assert rec["url"] == "https://www.facebook.com/P1/videos/FV1"
        rec = publish.publish(p, s, {"platform": "facebook", "kind": "reel", "target": t1, "file": str(vid),
                                     "title": "Reel", "description": "Ngắn", "hashtags": "#a #b"})
        assert fake.st["reel"] == data and fake.st["reel_finish"]["video_state"] == "PUBLISHED"
        assert fake.st["reel_finish"]["description"] == "Ngắn\n\n#a #b" and rec["url"].endswith("/reel/R1")
        assert len(publish.load_posts(p)) == 2
        publish.remove_account("fb:P2")
        assert [x["id"] for x in publish.load_accounts("facebook")] == ["P1"]
    finally:
        fake.close()


def test_publish_zernio_accounts_upload_post_and_status(monkeypatch, tmp_path):
    from trami import config, publish
    from trami.project import Project

    fake = _FakeSocial()
    fake.patch(monkeypatch)
    saved = {}
    monkeypatch.setattr(config, "save_settings", lambda d: saved.update(d))
    try:
        s = {"zernio_api_key": "ZKEY"}
        zn = publish.Zernio("ZKEY")
        assert zn.connect_url(s, "youtube") == "https://zernio.example/connect/yt" and saved["zernio_profile_id"] == "PR1"
        accs = zn.accounts()
        assert [(a["platform"], a["name"]) for a in accs] == [("youtube", "Kênh Z"), ("facebook", "Trang Z")]
        t = next(x for x in publish.targets("youtube") if x["via"] == "zernio")
        vid = tmp_path / "v.mp4"
        data = os.urandom(200 * 1024)
        vid.write_bytes(data)
        p = Project.create("zn")
        rec = publish.publish(p, s, {"platform": "youtube", "kind": "video", "target": t, "file": str(vid),
                                     "title": "Tiêu đề", "description": "Mô tả", "hashtags": "#x", "privacy": "unlisted",
                                     "synthetic": True})
        assert fake.st["zn"] == data
        body = fake.st["zn_post"]
        assert body["publishNow"] is True and body["mediaItems"][0]["url"] == "https://media.zernio.com/v.mp4"
        psd = body["platforms"][0]["platformSpecificData"]
        assert body["platforms"][0]["accountId"] == "A1" and psd["visibility"] == "unlisted"
        assert psd["containsSyntheticMedia"] is True and body["content"] == "Mô tả\n\n#x"
        assert rec["status"] == "published" and rec["url"] == "https://www.youtube.com/watch?v=ZZ"
        # Facebook Reels hẹn giờ qua Zernio: không chờ, gửi scheduledFor (UTC)
        from datetime import datetime, timedelta

        tf = next(x for x in publish.targets("facebook") if x["via"] == "zernio")
        rec = publish.publish(p, s, {"platform": "facebook", "kind": "reel", "target": tf, "file": str(vid),
                                     "title": "R", "description": "D",
                                     "publish_at": (datetime.now() + timedelta(hours=3)).isoformat()})
        body = fake.st["zn_post"]
        assert "publishNow" not in body and body["scheduledFor"].endswith("Z") and rec["status"] == "scheduled"
        assert body["platforms"][0]["platformSpecificData"] == {"contentType": "reel", "title": "R"}
    finally:
        fake.close()


def test_publish_text_manual_and_job_rules(tmp_path):
    from trami import jobs, publish

    assert publish.norm_tags("#Phim hay, #phim  hoạt_hình #") == ["Phim", "hay", "hoạt_hình"]
    m = {"title": "T", "description": "D", "hashtags": ["a"]}
    assert publish.compose_text(m, "facebook", "video", with_title=True) == "T\n\nD\n\n#a"
    assert publish.parse_when("2000-01-01T00:00:00") is None and publish.parse_when("") is None
    vid = tmp_path / "x.mp4"
    vid.write_bytes(b"0")
    text, txt, url = publish.prepare_manual(vid, m, "youtube", "short")
    assert url == "https://www.youtube.com/upload" and txt.name == "x.youtube_short.txt"
    assert text == "T\n\nD\n\n#Shorts #a" and "TIÊU ĐỀ:\nT" in txt.read_text(encoding="utf-8")
    # đang chạy lần lượt: đăng video (chỉ mạng) không chờ việc khác và không chặn việc khác
    m_ = jobs.JobManager.__new__(jobs.JobManager)
    m_.jobs, m_.running = {}, set()
    mk = lambda i, kind, pid="p": jobs.Job(id=i, kind=kind, title=i, project_id=pid, _fn=None,  # noqa: E731
                                           gpu=kind == "dub")
    m_.jobs = {"d": mk("d", "dub"), "u": mk("u", "publish"), "r": mk("r", "render", "q")}
    m_.running = {"d"}
    assert m_._why_wait(m_.jobs["u"]) == ""
    m_.running = {"u"}
    assert m_._why_wait(m_.jobs["r"]) == "" and m_._why_wait(m_.jobs["d"]) == ""


def test_publish_gui_manual_and_job(sample_video, monkeypatch):
    """Tab Xuất → Đăng video: nạp bản xuất mới nhất, Tự tải lên (chép nội dung, mở trang đăng, ghi lịch sử), đăng qua
    API = việc nền "publish" (không chờ việc khác), xong thì bảng Đã đăng cập nhật."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:
        pytest.skip(str(e))
    import time as _t

    from trami import pipeline, publish
    from trami.gui import publish_ui, theme
    from trami.gui.main import MainWindow
    from trami.gui.widgets import set_combo
    from trami.jobs import manager
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    p = Project.create("pubgui")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(0.5, 1.5, "你好", "Xin chào", "S1")]
    p.save()
    p.add_output(Path(sample_video), "video")
    publish.save_accounts("youtube", [{"id": "UCX", "name": "Kênh X", "refresh_token": "r"}])
    opened = []
    monkeypatch.setattr(publish_ui.QDesktopServices, "openUrl", lambda u: opened.append(u.toString()))
    monkeypatch.setattr(publish_ui, "_reveal", lambda path: None)
    win = MainWindow()
    win.show()
    win.open_project(p.id)
    pp = win.project_page
    pp.tabs.set_current(pp.TAB_EXPORT)
    app.processEvents()
    pub = pp.pub
    assert pub.file == Path(sample_video) and pub.plat["youtube"]["target"].currentData() == "yt:UCX"
    pub.title.setText("Tiêu đề thử")
    pub.desc.setPlainText("Mô tả")
    pub.tags.setText("#a b")
    set_combo(pub.plat["youtube"]["target"], "manual:youtube")
    pub._go()
    assert opened == ["https://www.youtube.com/upload"]
    assert QApplication.clipboard().text() == "Tiêu đề thử\n\nMô tả\n\n#a #b"
    assert publish.load_posts(p)[-1]["status"] == "prepared" and pub.hist.rowCount() == 1
    assert publish.load_draft(p)["youtube_target"] == "manual:youtube"
    # đăng qua API riêng -> việc nền
    calls = []

    def fake_publish(pr, s, item, progress=None):
        calls.append(item)
        rec = {"id": "r1", "time": _t.time(), "platform": item["platform"], "kind": item["kind"], "via": "api",
               "account": item["target"]["name"], "title": item["title"], "url": "https://youtu.be/V", "status":
               "published", "warn": ""}
        publish.save_post(pr, rec)
        return rec
    monkeypatch.setattr(publish, "publish", fake_publish)
    set_combo(pub.plat["youtube"]["target"], "yt:UCX")
    pub._go()
    for _ in range(100):
        app.processEvents()
        js = [j for j in manager.list(p.id) if j["kind"] == "publish"]
        if js and js[0]["status"] in ("done", "error"):
            break
        _t.sleep(0.05)
    assert js[0]["status"] == "done" and "https://youtu.be/V" in js[0]["message"]
    assert calls[0]["target"]["id"] == "yt:UCX" and calls[0]["hashtags"] == "#a b" and calls[0]["lang"] == "vi"
    pp.job_finished(js[0])
    assert pub.hist.rowCount() == 2
    win.close()


_FAKE_YT = """<html><body>
<ytcp-uploads-dialog><ytcp-uploads-file-picker><input type="file" id="f"></ytcp-uploads-file-picker>
<div id="form" style="display:none">
 <div id="title-textarea"><div id="textbox" contenteditable="true">ten_file</div></div>
 <div id="description-textarea"><div id="textbox" contenteditable="true"></div></div>
 <ytcp-thumbnails-compact-editor-uploader><input type="file" id="th"></ytcp-thumbnails-compact-editor-uploader>
 <div name="VIDEO_MADE_FOR_KIDS_NOT_MFK" onclick="window.kids='no'">Không dành cho trẻ em</div>
 <div name="VIDEO_MADE_FOR_KIDS_MFK" onclick="window.kids='yes'">Dành cho trẻ em</div>
 <div id="next-button" onclick="window.step=(window.step||0)+1">Tiếp</div>
 <div name="PUBLIC" onclick="window.vis='public'">Công khai</div>
 <div name="UNLISTED" onclick="window.vis='unlisted'">Không công khai</div>
 <a href="https://youtu.be/ABC123">https://youtu.be/ABC123</a>
 <div id="done-button" onclick="window.done=1;document.body.insertAdjacentHTML('beforeend',
   '<ytcp-video-share-dialog><a href=&quot;https://youtu.be/ABC123&quot;>link</a></ytcp-video-share-dialog>')">Xuất bản</div>
</div></ytcp-uploads-dialog>
<script>document.getElementById('f').onchange=()=>{window.fname=document.getElementById('f').files[0].name;
 setTimeout(()=>document.getElementById('form').style.display='block',300)};
 document.getElementById('th').onchange=()=>{window.thumb=1};</script></body></html>"""

_FAKE_TT = """<html><body><input type="file" accept="video/*" id="f">
<div id="ed" style="display:none" class="DraftEditor-root"><div class="public-DraftEditor-content" contenteditable="true">x.mp4</div></div>
<button data-e2e="post_video_button" onclick="window.posted=1">Đăng</button>
<script>document.getElementById('f').onchange=()=>{document.getElementById('ed').style.display='block'};</script>
</body></html>"""

_FAKE_FB = """<html><body><button id="add" onclick="const i=document.createElement('input');i.type='file';
 i.onchange=()=>{window.fbfile=i.files[0].name};i.click()">Thêm video</button>
<div role="textbox" contenteditable="true" id="cap" style="min-height:40px"></div>
<button onclick="window.posted=1">Đăng</button></body></html>"""


def test_browser_post_fills_and_stops_before_post(monkeypatch, tmp_path):
    """Mở Chrome & điền sẵn: chromium thật (chạy ẩn) trên 3 trang giả – gắn file, điền tiêu đề / mô tả / hashtag, chọn
    khán giả + chế độ hiển thị, KHÔNG bấm Đăng; bấm Đăng (YouTube) -> nhận ra đã đăng + link; đóng tab -> xong."""
    pytest.importorskip("playwright")
    import glob
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from trami import browser_post as bp, publish
    from trami.project import Project

    exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [""])[0]
    if not exe:
        pytest.skip("không có chromium")
    pages = {"/yt": _FAKE_YT, "/tt": _FAKE_TT, "/fb": _FAKE_FB}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            b = pages.get(self.path.split("?")[0], "").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    monkeypatch.setattr(bp, "HEADLESS", True)
    monkeypatch.setattr(bp, "YT_URL", base + "/yt")
    monkeypatch.setattr(bp, "TT_URL", base + "/tt")
    monkeypatch.setattr(bp, "FB_URLS", {"video": base + "/fb", "reel": base + "/fb"})
    monkeypatch.setattr(bp, "WAIT_FORM", 10)
    monkeypatch.setattr(publish, "check_video", lambda *a: ("", ""))
    s = {"browser_exe": exe}
    vid = tmp_path / "phim.mp4"
    vid.write_bytes(b"0" * 1000)
    thumb = tmp_path / "bia.jpg"
    thumb.write_bytes(b"\xff\xd8")
    eng = bp.engine()
    js = lambda tok, expr: eng.call(lambda e: e.pages[tok][0].evaluate(expr))  # noqa: E731
    try:
        assert bp.problem(s) == ""
        # YouTube: điền rồi dừng; "bấm Xuất bản" -> nhận ra đã đăng
        item = {"platform": "youtube", "kind": "short", "file": str(vid), "title": "Phim <hay>", "description": "Dòng 1\nDòng 2",
                "hashtags": ["phim"], "privacy": "unlisted", "thumbnail": str(thumb)}
        tok, rep = eng.call(bp._do_fill, s, "main", item)
        assert rep["need_login"] is False and rep["miss"] == [], rep
        assert {"gắn file video", "tiêu đề", "mô tả", "ảnh bìa", "chế độ hiển thị"} <= set(rep["done"])
        assert js(tok, "window.fname") == "phim.mp4" and js(tok, "window.thumb") == 1
        assert js(tok, "document.querySelector('#title-textarea #textbox').innerText") == "Phim hay"
        d = js(tok, "document.querySelector('#description-textarea #textbox').innerText")
        assert "Dòng 1" in d and "Dòng 2" in d and "#Shorts #phim" in d
        assert js(tok, "[window.kids, window.step, window.vis, window.done || 0]") == ["no", 3, "unlisted", 0]
        assert rep["url"] == "https://youtu.be/ABC123"
        assert js(tok, "document.querySelector('#done-button').style.outlineStyle") == "solid"   # viền cam, chưa bấm
        assert eng.call(bp._state, tok) == {"closed": False, "published": False, "url": "https://youtu.be/ABC123"}
        js(tok, "document.querySelector('#done-button').click()")
        assert eng.call(bp._state, tok)["published"] is True
        # TikTok qua publish.publish: chờ tới khi đóng tab
        p = Project.create("br")
        t = next(x for x in publish.targets("tiktok") if x["via"] == "browser")
        assert [x["via"] for x in publish.targets("tiktok")] == ["browser", "manual"]
        box = {}
        th = threading.Thread(target=lambda: box.update(r=publish.publish(
            p, s, {"platform": "tiktok", "kind": "video", "target": t, "file": str(vid), "title": "T",
                   "description": "Hay quá", "hashtags": "#a #b"}, lambda f, m: box.update(msg=m))))
        th.start()
        for _ in range(100):
            if eng.pages and "Đã điền sẵn" in box.get("msg", ""):
                break
            time.sleep(0.1)
        [(ttok, (page_tt, plat, _r))] = [(k, v) for k, v in eng.pages.items() if v[1] == "tiktok"]
        cap = eng.call(lambda e: page_tt.evaluate("document.querySelector('.public-DraftEditor-content').innerText"))
        assert cap.replace("\n\n", "\n").split("\n") == ["Hay quá", "#a #b"] or "Hay quá" in cap and "#a #b" in cap
        assert eng.call(lambda e: page_tt.evaluate("window.posted || 0")) == 0
        eng.call(lambda e: page_tt.close())
        th.join(10)
        assert box["r"]["status"] == "browser" and box["r"]["via"] == "browser"
        assert publish.load_posts(p)[-1]["platform"] == "tiktok"
        # Facebook: không có ô chọn file -> bấm "Thêm video" và đưa file vào hộp chọn file
        tok, rep = eng.call(bp._do_fill, s, "main", {"platform": "facebook", "kind": "video", "file": str(vid),
                                                     "title": "", "description": "Mô tả FB", "hashtags": ["x"]})
        assert "gắn file video" in rep["done"] and "nội dung bài đăng" in rep["done"], rep
        assert js(tok, "window.fbfile") == "phim.mp4" and js(tok, "window.posted || 0") == 0
        assert "Mô tả FB" in js(tok, "document.getElementById('cap').innerText")
    finally:
        eng.close_profile("main")
        srv.shutdown()


# ------------------------------------------------------------------ 2.0.79: tỉ lệ khung, lớp phủ chữ / logo, rút khoảng lặng

def _frame_rgb(path, t, size=None):
    """Đọc 1 khung (RGB numpy) của video tại t giây."""
    from trami import ffmpeg as _ff

    info = _ff.probe(path)
    w, h = size or (info.width, info.height)
    r = subprocess.run([_ff.ffmpeg_bin(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w, 3)


def test_aspect_presets_fit_geometry_and_ratio_override():
    from trami import video

    assert set(video.PORTRAIT) | set(video.LANDSCAPE) <= set(video.ASPECTS)
    assert len(set(video.PORTRAIT + video.LANDSCAPE)) == 13
    assert video.aspect_wh("9:16") == (9, 16) and video.aspect_wh("original") is None
    assert video.target_size(1920, 1080, "9:16", "1080p") == (1080, 1920)
    assert video.target_size(1920, 1080, "5:7", "original") == (1080, 1512)
    # phim ngang 16:9 trong khung dọc 9:16: vừa khít chiều ngang, lệch lên / xuống theo vị trí
    x, y, w, h = video.fit_geometry(1920, 1080, 1080, 1920, {"aspect_mode": "blur", "aspect_pos": [0.5, 0.5]})
    assert (round(x), round(w), round(h)) == (0, 1080, 608) and abs(y - (1920 - 607.5) / 2) < 1
    _, y2, _, _ = video.fit_geometry(1920, 1080, 1080, 1920, {"aspect_pos": [0.5, 0.15]})
    assert y2 < y
    # phóng cho đầy khung: phủ kín, tràn 2 bên
    x, y, w, h = video.fit_geometry(1920, 1080, 1080, 1920, {"aspect_mode": "crop"})
    assert round(h) == 1920 and w > 1080 and x < 0
    # cỡ phim 150 %
    _, _, w, _ = video.fit_geometry(1920, 1080, 1080, 1920, {"aspect_scale": 1.5})
    assert round(w) == 1620
    e = {"aspect": "16:9", "aspect_mode": "blur", "layers": [{"id": "a", "x": 0.5, "y": 0.1}],
         "subtitles": {"style": {"position": "bottom"}},
         "ratio_over": {"9:16": {"aspect_mode": "color", "layers": {"a": {"y": 0.3}}, "sub": {"pos_y": 0.7}}}}
    r = video.edit_for_ratio(e, "9:16")
    assert r["aspect"] == "9:16" and r["aspect_mode"] == "color" and r["layers"][0]["y"] == 0.3
    assert r["subtitles"]["style"]["pos_y"] == 0.7 and e["layers"][0]["y"] == 0.1     # bản chính không đổi


def test_overlay_templates_render_and_migrate():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from trami import overlay

    overlay.ensure_qt()
    for code, _name, _p in overlay.TEMPLATES + overlay.LOGO_TEMPLATES:
        L = overlay.new_layer("text", template=code, channel="Phim Hay TV")
        img, ox, oy = overlay.render(L, 1280, 720)
        assert img.width() > 20 and img.height() > 10 and 0 < ox < img.width() and 0 < oy < img.height(), code
        assert "{" not in L["text"]
    assert overlay.new_layer("text", template="ch_circle", channel="Phim Hay")["text"] == "PH"
    # xoay 90°: ảnh đổi chiều
    L = overlay.new_layer("text", template="black_round")
    a, _, _ = overlay.render(L, 1280, 720)
    b, _, _ = overlay.render(dict(L, rot=90), 1280, 720)
    assert abs(b.width() - a.height()) <= 6 and abs(b.height() - a.width()) <= 6
    # dự án cũ: logo + chữ chạy -> lớp phủ
    e = {"watermark": {"path": "/x/logo.png", "position": "top-right", "scale": 0.2, "opacity": 0.7},
         "text": {"content": "Vietsub  by  me", "position": "scroll", "color": "#FF0000"}}
    assert overlay.migrate(e) and not overlay.migrate(e)
    lg, tx = e["layers"]
    assert lg["kind"] == "logo" and lg["w"] == 0.2 and lg["opacity"] == 0.7 and lg["x"] > 0.8 and lg["y"] < 0.2
    assert tx["text"] == "Vietsub by me" and tx["anim"] == "scroll" and tx["color"] == "#FF0000"
    assert e["watermark"]["path"] == "" and e["text"]["content"] == ""
    # hiệu ứng: xem trước và biểu thức FFmpeg cùng công thức
    L = overlay.new_layer("text", anim="slide_l", fade_in=0.5)
    x0, _dy, a0 = overlay.anim_state(L, 0.0, 5, 1280, 720, 200, 50, 100)
    x1, _dy, a1 = overlay.anim_state(L, 0.5, 5, 1280, 720, 200, 50, 100)
    assert x0 == 100 + 1280 * 0.5 + 200 and x1 == 100 and a0 == 0 and a1 == 1
    xe, _ye = overlay.ffmpeg_xy(L, 2.0, 100, 50, 1280, 720, 200, 50)
    assert "pow(1-min(1" in xe and "(t-2.000)/0.500" in xe
    assert overlay.out_span({"start": 2, "end": 4}, 10, [(0, 1), (3, 10)]) == (1.0, 2.0)
    assert overlay.out_span({"start": 1.2, "end": 2.8}, 10, [(0, 1), (3, 10)]) is None


def test_render_aspect_color_layers_logo_gif(sample_video, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    import json as _json

    from PIL import Image

    from trami import overlay, video
    from trami.config import load_settings
    from trami.project import DEFAULT_EDIT

    logo = tmp_path / "logo.png"
    Image.new("RGBA", (100, 100), (255, 0, 255, 255)).save(logo)
    gif = tmp_path / "a.gif"
    frames = [Image.new("RGB", (40, 40), c) for c in ((0, 255, 0), (0, 0, 255))]
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=200, loop=0)
    spec = _json.loads(_json.dumps(DEFAULT_EDIT))
    spec.update(aspect="9:16", aspect_mode="color", aspect_color="#204080", aspect_pos=[0.5, 0.2], resolution="480p")
    spec["cuts"] = [[1.0, 2.0]]
    spec["layers"] = [overlay.new_layer("logo", path=str(logo), w=0.2, x=0.5, y=0.85, start=2.5, end=5.0),
                      overlay.new_layer("logo", path=str(gif), w=0.2, x=0.2, y=0.6),
                      overlay.new_layer("text", template="brush_yellow", text="TIÊU ĐỀ", y=0.1)]
    out = video.build_and_render(sample_video, tmp_path / "o.mp4", spec, load_settings(), tmp_path / "w", [])
    info = ff.probe(out)
    assert (info.width, info.height) == (480, 854) and abs(info.duration - 5.0) < 0.15
    f = _frame_rgb(out, 3.0)     # giây xuất 3,0 = giây gốc 4,0 -> logo đang hiện
    cy = int(854 * 0.85)
    assert f[cy, 240, 0] > 200 and f[cy, 240, 1] < 70 and f[cy, 240, 2] > 200          # logo hồng tím
    assert abs(int(f[int(854 * 0.97), 20, 2]) - 0x80) < 30                               # nền màu
    f1 = _frame_rgb(out, 0.5)    # trước lúc logo hiện (giây gốc 0,5)
    assert not (f1[cy, 240, 0] > 200 and f1[cy, 240, 1] < 70)
    g = f[int(854 * 0.6), int(480 * 0.2)]
    assert g[1] > 150 or g[2] > 150                                                      # GIF động
    band = f[int(854 * 0.06):int(854 * 0.14)].reshape(-1, 3).astype(int)
    assert ((band[:, 0] > 200) & (band[:, 1] > 170) & (band[:, 2] < 90)).sum() > 500      # nét cọ vàng của tiêu đề
    # cùng 1 lớp, vẽ 2 lần: dùng lại ảnh PNG đã vẽ
    n = len(list((tmp_path / "w" / "layers").glob("layer_*.png")))
    video.build_and_render(sample_video, tmp_path / "o2.mp4", spec, load_settings(), tmp_path / "w", [])
    assert len(list((tmp_path / "w" / "layers").glob("layer_*.png"))) == n == 2


def test_chunked_render_with_layers_matches_whole(sample_video, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    import json as _json

    from trami import overlay, video
    from trami.config import load_settings
    from trami.project import DEFAULT_EDIT

    spec = _json.loads(_json.dumps(DEFAULT_EDIT))
    spec["layers"] = [overlay.new_layer("text", template="black_round", text="CHỮ", start=2.2, end=4.6,
                                        anim="fade", fade_in=0.4, fade_out=0.4, x=0.5, y=0.3)]
    st = load_settings()
    whole = video.build_and_render(sample_video, tmp_path / "whole.mp4", spec, dict(st, export_chunks=False),
                                   tmp_path / "w1", [])
    monkeypatch.setattr(video, "CHUNK_SEC", 2.0)
    monkeypatch.setattr(video, "CHUNK_MIN_DUR", 1.0)
    chunk = video.build_and_render(sample_video, tmp_path / "chunk.mp4", spec, st, tmp_path / "w2", [])
    assert len(list((tmp_path / "w2" / "chunks").glob("c_*.mp4"))) == 3
    for t in (1.0, 2.4, 3.0, 4.4, 5.0):
        a, b = _frame_rgb(whole, t).astype(int), _frame_rgb(chunk, t).astype(int)
        assert np.abs(a - b).mean() < 3.0, t


def test_silence_gaps_songs_and_summary(tmp_path):
    from trami import silence

    segs = [Segment(0.5, 2, "a", "a"), Segment(12, 14, "b", "b"), Segment(14.5, 16, "c", "c"),
            Segment(30, 33, "la", "♪", extra={"song": True}), Segment(35, 38, "la", "♪", extra={"song": True}),
            Segment(50, 52, "d", "d"), Segment(52.5, 54, "e", "e", extra={"dub_at": [52.5, 58.5]})]
    o = silence.options({}, min=4, keep=1.5)
    g = silence.find_gaps(segs, 70, o)
    assert [(x["kind"], x["cut"]) for x in g] == [("gap", [2.75, 11.25]), ("gap", [16.75, 29.25]),
                                                    ("gap", [38.75, 49.25]), ("tail", [60.0, 70.0])]
    # giọng lồng đọc dài hơn câu (dub_at tới 58,5 s) -> khoảng lặng cuối bắt đầu từ 58,5
    assert g[-1]["a"] == 58.5
    # đoạn dài hơn mức tối đa: để nguyên; rút còn 0 = xoá hẳn; giữ phần sau câu trước
    assert [x["a"] for x in silence.find_gaps(segs, 70, dict(o, max=12))] == [2.0, 38.0, 58.5]   # 14 s > 12: bỏ
    assert silence.find_gaps(segs, 70, dict(o, keep=0))[0]["cut"] == [2.0, 12.0]
    assert silence.find_gaps(segs, 70, dict(o, where="after"))[0]["cut"] == [3.5, 12.0]
    assert silence.find_gaps(segs, 70, dict(o, skip_head=5))[0]["a"] == 5.0
    # không cắt 🎵: khoảng lặng giữa 2 câu hát (33–35 s) bỏ qua; tắt -> vẫn bỏ (ngắn hơn 4 s)
    assert all(not (30 <= x["a"] < 38) for x in g)
    s = silence.find_songs(segs, 70, o)
    assert s == [{"id": "s30.0", "kind": "song", "a": 16.3, "b": 49.7, "cut": [16.3, 49.7], "on": True, "lines": 2}]
    items = silence.merge_items(g, s)
    assert [x["kind"] for x in items] == ["gap", "song", "tail"]
    items[0]["on"] = False
    n, tot, before, after = silence.summary(items, 70, {}, [[60.5, 61]])
    assert (n, tot) == (2, 3) and abs(before - 69.5) < 1e-6 and abs(after - (70 - 33.4 - 10)) < 1e-6
    assert silence.new_cuts([[60.5, 61]], items) == [[16.3, 49.7], [60.0, 70.0]]
    assert silence.preset_of({"min": 2, "keep": 0.5}) == "strong" and silence.preset_of({"min": 3, "keep": 1}) == "custom"


def test_silence_loudness_quiet_spans(tmp_path):
    from trami import silence

    wav = tmp_path / "a.wav"
    subprocess.run([ff.ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=mono:d=3", "-f", "lavfi", "-i", "sine=f=440:d=2", "-filter_complex",
                    "[0:a][1:a][2:a]concat=n=3:v=0:a=1", str(wav)], check=True)
    db = silence.loudness(wav, tmp_path / "w", duration=7)
    assert silence.has_loudness(wav, tmp_path / "w") and abs(len(db) * silence.HOP - 7) < 0.1
    q = silence.quiet_spans(db, -30)
    assert len(q) == 1 and abs(q[0][0] - 2) < 0.1 and abs(q[0][1] - 5) < 0.1
    items = silence.find_gaps([], 7, silence.options({}, by="volume", min=2, keep=1), db)
    assert len(items) == 1 and abs(items[0]["cut"][0] - 2.5) < 0.1 and abs(items[0]["cut"][1] - 4.5) < 0.1


def test_render_extra_ratios_and_publish_picks_by_ratio(sample_video, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from trami import pipeline, publish
    from trami.project import Project

    p = Project.create("ratios")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(0.5, 2, "你好", "Xin chào")]
    e = p.edit
    e.update(aspect="16:9", resolution="480p", extra_ratios=["9:16", "1:1"],
             ratio_over={"9:16": {"aspect_mode": "color", "aspect_color": "#FF0000"}})
    p.data["edit"] = e
    p.save()
    main = pipeline.render(p, dict(pipeline.load_settings(), export_chunks=False))
    outs = [o for o in Project(p.id).data["outputs"] if o["kind"] == "video"]
    assert [o["ratio"] for o in outs] == ["16:9", "9:16", "1:1"] and len({o["batch"] for o in outs}) == 1
    assert (outs[0]["w"], outs[0]["h"]) == (854, 480) and (outs[1]["w"], outs[1]["h"]) == (480, 854)
    assert outs[1]["path"].endswith("_9x16.mp4") and Path(main) == Path(outs[0]["path"])
    f = _frame_rgb(outs[1]["path"], 1.0)
    assert f[10, 240, 0] > 200 and f[10, 240, 1] < 60      # bản 9:16 dùng nền đỏ riêng của nó
    p = Project(p.id)
    assert publish.latest_video(p) == Path(outs[0]["path"])
    assert publish.video_for(p, "tiktok", "video", Path(main)) == Path(outs[1]["path"])
    assert publish.video_for(p, "youtube", "short", Path(main)) == Path(outs[1]["path"])
    assert publish.video_for(p, "youtube", "video", Path(main)) == Path(main)
    assert publish.video_for(p, "youtube", "video", Path(outs[1]["path"])) == Path(main)
    assert sorted(publish.batch_ratios(p, Path(main))) == ["1:1", "9:16"]


def test_gui_aspect_layers_timeline_silence_undo(sample_video, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from trami import overlay, pipeline
    from trami.gui import theme
    from trami.gui.main import MainWindow
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    p = Project.create("layers")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(0.2, 1.0, "你好", "Xin chào", "S1"), Segment(5.0, 5.8, "再见", "Tạm biệt", "S1")]
    e = p.edit
    e["text"] = {"content": "Vietsub", "position": "top-left", "color": "#FFFFFF"}
    p.data["edit"] = e
    p.save()
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.open_project(p.id)
    pp = win.project_page
    for _ in range(10):
        app.processEvents()
    # chữ cũ -> lớp phủ (đã lưu), có dòng riêng trên timeline
    assert [L["text"] for L in pp._layers] == ["Vietsub"] and Project(p.id).edit["text"]["content"] == ""
    assert any(k.startswith("L:") for k, *_ in pp.tracks.canvas.tracks())
    # tỉ lệ: chọn ở tab Chỉnh sửa -> tab Xuất cũng đổi (1 giá trị chung)
    pp._aspect_changed("9:16")
    assert pp.x_aspect.value == "9:16" and pp.e_aspect.value == "9:16"
    pp.x_aspect._chip_clicked("4:5")
    assert pp.e_aspect.value == "4:5"
    pp._amode_changed("color")
    assert pp.x_amode.currentData() == "color"
    pp._save_edit_now()
    assert Project(p.id).edit["aspect"] == "4:5" and Project(p.id).edit["aspect_mode"] == "color"
    # thêm mẫu chữ cọ vàng, kéo trên video (ô Ngang / Dọc cập nhật), kéo trên timeline
    pp.layers_tab.add_text("brush_yellow")
    lid = pp.layers_tab.sel
    pp._apply_live()
    app.processEvents()
    assert lid in pp.player.view.layers and pp.tracks.sel_layer == lid
    pp.player.view.on_layer_changed(lid, {"x": 0.3, "y": 0.4})
    assert abs(pp.layers_tab.p_x.value() - 30) < 1e-6
    pp.tracks.layerTimed.emit(lid, 1.0, 3.0)
    pp._save_edit_now()
    L = next(x for x in Project(p.id).edit["layers"] if x["id"] == lid)
    assert (L["x"], L["y"], L["start"], L["end"]) == (0.3, 0.4, 1.0, 3.0)
    # đầu phát ngoài khoảng hiện -> lớp ẩn trên trình phát
    pp._apply_live()
    pp.player.update_layers(0.5)
    assert not pp.player.view.layers[lid].isVisible()
    pp.player.update_layers(2.0)
    assert pp.player.view.layers[lid].isVisible()
    # xem thử tỉ lệ xuất thêm: kéo lớp = chỉnh riêng cho tỉ lệ đó, bản chính giữ nguyên
    pp.x_extra._toggle("16:9")
    pp._ratio_preview("16:9")
    pp.player.view.on_layer_changed(lid, {"x": 0.7})
    pp._ratio_preview("")
    pp._save_edit_now()
    e = Project(p.id).edit
    assert e["extra_ratios"] == ["16:9"] and e["ratio_over"]["16:9"]["layers"][lid] == {"x": 0.7}
    assert next(x for x in e["layers"] if x["id"] == lid)["x"] == 0.3
    # thư viện logo + thả vào video
    from PIL import Image

    img = tmp_path / "lg.png"
    Image.new("RGBA", (64, 32), (0, 200, 0, 255)).save(img)
    new = overlay.add_logos([img])
    pp.layers_tab.fill_library()
    pp.layers_tab.drop("lib:" + new[0]["id"], 0.8, 0.1)
    assert pp._layers[-1]["kind"] == "logo" and pp._layers[-1]["x"] == 0.8
    pp._layer_action(pp._layers[-1]["id"], "delete")
    assert all(x["kind"] != "logo" for x in pp._layers)
    # vùng an toàn: lớp ở sát trên bị báo + đưa vào vùng an toàn
    pp.e_safe.setChecked(True)
    pp.player.view.on_layer_changed(lid, {"y": 0.03})
    pp._apply_live()
    app.processEvents()
    pp.player.update_layers(2.0)  # (trình phát có thể vừa báo vị trí 0 -> lớp 1–3 s bị ẩn)
    assert any("TIÊU ĐỀ" in x for x in pp.player.view.safe_issues())
    pp._safe_fix()
    assert next(x for x in pp._layers if x["id"] == lid)["y"] > 0.12
    # rút khoảng lặng: tìm, bỏ tích 1 đoạn, áp dụng -> cuts; Ctrl+Z hoàn tác
    pp.sil.s_min.setValue(2.0)
    pp.sil.s_keep.setValue(0.5)
    pp.sil.scan()
    assert pp.sil.items and pp.tracks.pending == pp.sil.items
    cut = list(pp.sil.items[0]["cut"])
    pp.sil.applyRequested.emit([it for it in pp.sil.items if it.get("on")])
    assert Project(p.id).edit["cuts"] == [cut] and pp.sil.items == []
    pp.undo()
    assert Project(p.id).edit["cuts"] == []
    pp.redo()
    assert Project(p.id).edit["cuts"] == [cut]
    for i in range(10):
        pp.tabs.set_current(i)
        app.processEvents()
    win.close()


def test_split_long_translations_at_commas(sample_video):
    from trami import align, pipeline, tts
    from trami.project import Project

    s1 = Segment(3710.3, 3714.5, "为俺们天剑宗变穷的账，为大师兄挨的雷，",
                 "Vì món nợ khiến Thiên Kiếm Tông nghèo rớt, vì những đạo sét đại sư huynh phải chịu,", "S1",
                 extra={"voice": "edge:vi-VN-HoaiMyNeural", "tts_file": "x.mp3", "dub_at": [3710.3, 3714.6]})
    short = Segment(0, 2, "好的，走吧", "Được, đi thôi", "S1")
    mismatch = Segment(10, 15, "我们今天一定要去那个地方看一看", "Hôm nay, nhất định chúng ta phải đến nơi đó, xem cho biết",
                       "S1")
    out, made = align.split_long_lines([short, s1, mismatch])
    assert made == [1, 2] and out[0] is short and out[3] is mismatch
    a, b = out[1], out[2]
    assert (a.text, a.trans) == ("为俺们天剑宗变穷的账，", "Vì món nợ khiến Thiên Kiếm Tông nghèo rớt,")
    assert (b.text, b.trans) == ("为大师兄挨的雷，", "vì những đạo sét đại sư huynh phải chịu,")
    assert a.start == 3710.3 and b.end == 3714.5 and a.end <= b.start
    assert b.extra["pause_before"] == 0.25 and "pause_before" not in a.extra
    assert a.extra["voice"] == b.extra["voice"] and "tts_file" not in a.extra and "dub_at" not in b.extra
    # có chỗ ngắt hơi thật trong giọng -> mốc đặt đúng chỗ lặng
    hop = 0.05
    env = np.full(int(3721 / hop), -20.0, np.float32)
    env[int(3712.4 / hop):int(3712.7 / hop)] = -55
    (a, b), _ = align.split_long_lines([s1], env=env, hop=hop)
    assert 3712.35 <= a.end <= 3712.5 and 3712.6 <= b.start <= 3712.75
    # mức tách cao hơn độ dài câu -> giữ nguyên; tiếng Anh quy đổi theo tốc độ nói
    assert align.split_long_lines([s1], max_units=25)[1] == []
    en = Segment(0, 4, "为俺们天剑宗变穷的账，为大师兄挨的雷，",
                 "For the debt that made our Sword Sect poor, for the lightning our senior took,", "S1")
    assert len(align.split_long_lines([en], "en")[0]) == 2
    # giọng lồng: vế sau nghỉ ít nhất 0,3 s sau vế trước
    plan = tts.plan_layout([{"k": 0, "want": 0, "dur": 2.6}, {"k": 1, "want": 2.5, "dur": 2.0, "pause": 0.3}], 10)
    assert plan[1]["start"] - plan[0]["end"] >= 0.3 - 1e-6
    # dự án: tách + hoàn tác
    p = Project.create("split")
    pipeline.attach_source(p, sample_video)
    p.segments = [short, Segment(0.5, 5.5, s1.text, s1.trans, "S1")]
    p.save()
    rows = pipeline.split_long(p)
    assert rows == [1, 2] and len(Project(p.id).segments) == 3
    assert pipeline.undo_split(Project(p.id)) == 2 and len(Project(p.id).segments) == 2


def test_split_long_at_sentence_ends_and_keep_split():
    from trami import align, skills

    a = Segment(100.0, 108.5, "就是，要不说这祸害能一千年呢得了，既然没死，那就还是帮本武长，接着管好这帮贱犯啊。",
                "Đúng vậy, người ta vẫn bảo tai họa để đời ngàn năm, đã không chết thì mau giúp Mã Vũ Trưởng ta, "
                "tiếp tục quản lý đám tù nhân hèn mọn này đi.", "S1")
    b = Segment(109.0, 114.6, "熊大，他说的帮他管犯人是什么意思？哥，你是咱流放营第三五的管事儿啊，",
                "Hùng Đại, hắn nói giúp hắn quản lý tù nhân là ý gì? Ca, ca là quản sự trại lưu đày của chúng ta mà,", "S2")
    out, made = align.split_long_lines([a, b])
    assert [s.trans for s in out] == [
        "Đúng vậy, người ta vẫn bảo tai họa để đời ngàn năm,",       # 4 dấu phẩy gốc / 3 dấu phẩy dịch: khớp theo vị trí
        "đã không chết thì mau giúp Mã Vũ Trưởng ta,",
        "tiếp tục quản lý đám tù nhân hèn mọn này đi.",
        "Hùng Đại, hắn nói giúp hắn quản lý tù nhân là ý gì?",       # ưu tiên chỗ hết câu (?) thay vì dấu phẩy
        "Ca, ca là quản sự trại lưu đày của chúng ta mà,"]
    assert [s.text for s in out[:3]] == ["就是，要不说这祸害能一千年呢得了，", "既然没死，那就还是帮本武长，", "接着管好这帮贱犯啊。"]
    assert out[3].text == "熊大，他说的帮他管犯人是什么意思？" and made == [0, 1, 2, 3, 4]
    assert [s.extra.get("pause_before", 0) for s in out] == [0, 0.25, 0.25, 0, 0.4]   # hết câu nghỉ lâu hơn
    assert all(out[k].end <= out[k + 1].start for k in range(4))
    # dấu trong số / chữ viết tắt không phải chỗ tách
    assert align._bounds("Giá 1,5 vạn, lúc 10:30 ở TP.HCM nhé", align.END_TR, ",;:") == [(12, "clause")]
    # lệnh nối câu (luật / AI) không nối lại các vế đã tách có chủ ý
    assert align.split_mates(out[0], out[1]) and not align.split_mates(out[2], out[3])
    joined, n = align.join_broken(out)
    assert n == 0 and len(joined) == 5
    new, st = skills.apply_cleanup(out, {"mode": "join", "merge": [[1, 2], [4, 5]]})
    assert len(new) == 5 and st["merged"] == 0


def test_gui_split_long_not_overwritten(sample_video, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    import threading
    import time as _t

    from PySide6.QtWidgets import QApplication

    from trami import pipeline
    from trami.gui import theme
    from trami.gui.main import MainWindow
    from trami.jobs import manager
    from trami.project import Project

    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    app.setStyleSheet(theme.stylesheet())
    p = Project.create("splitgui")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(0.3, 4.5, "熊大，他说的帮他管犯人是什么意思？哥，你是咱流放营第三五的管事儿啊，",
                          "Hùng Đại, hắn nói giúp hắn quản lý tù nhân là ý gì? Ca, ca là quản sự trại lưu đày của chúng "
                          "ta mà,", "S1"), Segment(5.0, 5.8, "好", "Được", "S1")]
    p.save()
    win = MainWindow()
    win.resize(1600, 1000)
    win.show()
    win.open_project(p.id)
    pp = win.project_page

    def pump(cond, t=15):
        t0 = _t.time()
        while not cond() and _t.time() - t0 < t:
            app.processEvents()
            _t.sleep(0.02)
        return cond()

    def idle():
        return not any(j["status"] in ("queued", "running") for j in manager.list() if j["project_id"] == p.id)

    # việc nền ghi danh sách câu mới (đã tách) trong lúc bảng còn hiện danh sách cũ -> giao diện không ghi đè lại
    gate, wrote = threading.Event(), threading.Event()

    def fn(j, q):
        pipeline.split_long(q)
        wrote.set()
        gate.wait(10)
        return {"message": "ok"}

    pp.submit("join", "Tách", fn)
    assert wrote.wait(10)
    pp._save_segs_now()
    assert len(Project(p.id).segments) == 3 and len(pp.model.segs) == 3
    gate.set()
    assert pump(idle)
    pp.run_undo_split()
    assert len(Project(p.id).segments) == 2
    # đã lồng tiếng: bước 1 tách (bảng có câu mới + tích sẵn), bước 2 mới đọc lại giọng các vế mới
    calls = []
    monkeypatch.setattr(pipeline, "has_dub", lambda q: True)
    real_submit = pp.submit

    def chained(rows, play=True):    # như _redub_rows thật: gửi việc mới ngay trong job_finished (poll lồng nhau)
        calls.append((rows, play))
        real_submit("redub", "Tạo lại giọng", lambda j, q: {"message": "ok"}, quiet=True)

    monkeypatch.setattr(pp, "_redub_rows", chained)
    pp.run_split_long()
    assert pump(lambda: calls)
    assert pump(idle)
    pump(lambda: False, 1.5)
    assert calls == [([0, 1], False)] and len(pp.model.segs) == 3 and len(Project(p.id).segments) == 3
    assert sum(1 for j in manager.list() if j["project_id"] == p.id and j["kind"] == "redub") == 1
    assert sorted(pp.model.selected) == [0, 1]
    assert pp.model.segs[0].trans.endswith("ý gì?")
    # công tắc "Tách câu dịch dài" ở tab Tự động = công tắc ở tab Phụ đề (1 cài đặt chung)
    from trami.config import load_settings

    pp.a_split_long.setChecked(False)
    assert not pp.s_split.isChecked() and load_settings()["split_long_trans"] is False
    pp.s_split.setChecked(True)
    assert pp.a_split_long.isChecked() and load_settings()["split_long_trans"] is True
    win.close()


def test_cli_and_batch_stop_before_export(sample_video, monkeypatch, capsys):
    """Dòng lệnh + Xử lý hàng loạt: mặc định dừng trước bước xuất video; --xuat-video / công tắc thì xuất luôn."""
    from trami import __main__ as cli, pipeline

    seen = []
    monkeypatch.setattr(pipeline, "auto", lambda p, o, *a, **k: seen.append(dict(o)) or {"srt": "", "message": "ok"})
    cli.main(["dich", str(sample_video)])
    cli.main(["dich", str(sample_video), "--xuat-video"])
    assert [o["render"] for o in seen] == [False, True]
    assert "Chưa xuất video" in capsys.readouterr().out
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from trami.gui.main import MainWindow
    from trami.jobs import manager

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    from trami.gui import main as gm

    page = next(w for w in win.findChildren(gm.BatchPage))
    subs = []
    monkeypatch.setattr(manager, "submit", lambda kind, title, fn, pid=None: subs.append(fn))
    page.items.setPlainText(str(sample_video))
    page.run()
    assert not page.do_render.isChecked()
    subs[0](type("J", (), {"update": staticmethod(lambda *a: None), "cancelled": staticmethod(lambda: False)})())
    assert seen[-1]["render"] is False
    page.do_render.setChecked(True)
    page.run()
    subs[-1](type("J", (), {"update": staticmethod(lambda *a: None), "cancelled": staticmethod(lambda: False)})())
    assert seen[-1]["render"] is True
    page.do_render.setChecked(False)
    win.close()
    app.processEvents()


def test_auto_translate_then_split_then_dub(sample_video, monkeypatch):
    """Chạy tự động: dịch XONG -> tách câu dịch dài -> lồng tiếng các vế; chạy lại trên dự án đã dịch chỉ dịch câu trống."""
    from trami import pipeline
    from trami.project import Project

    p = Project.create("autosplit")
    pipeline.attach_source(p, sample_video)
    p.segments = [Segment(0.3, 5.5, "熊大，他说的帮他管犯人是什么意思？哥，你是咱流放营第三五的管事儿啊，", "", "S1")]
    p.save()
    order = []

    def fake_translate(q, engine=None, style=None, only_empty=False, settings=None, progress=None, cancel=None,
                       rows=None, split=True, fresh=False):
        order.append(("translate", only_empty, split, len(q.segments)))
        segs = q.segments
        segs[0].trans = ("Hùng Đại, hắn nói giúp hắn quản lý tù nhân là ý gì? Ca, ca là quản sự trại lưu đày của "
                         "chúng ta mà,")
        q.segments = segs
        q.data["trans_lang"] = "vi"
        q.save()
        return 1

    monkeypatch.setattr(pipeline, "translate", fake_translate)
    monkeypatch.setattr(pipeline, "dub", lambda q, *a, **k: order.append(("dub", len(q.segments))))
    res = pipeline.auto(p, {"engine": "google", "dub": True, "render": False})
    assert order == [("translate", False, False, 1), ("dub", 2)]
    assert [s.trans for s in Project(p.id).segments] == [
        "Hùng Đại, hắn nói giúp hắn quản lý tù nhân là ý gì?", "Ca, ca là quản sự trại lưu đày của chúng ta mà,"]
    assert "tách câu dài" in res["message"]
    order.clear()
    pipeline.auto(Project(p.id), {"engine": "google", "dub": False, "render": False})
    assert order[0][:2] == ("translate", True)     # đã dịch -> chỉ dịch câu còn trống, không dịch đè câu đã sửa
    from trami.config import save_settings

    save_settings({"split_long_trans": False})
    try:
        order.clear()
        pipeline.auto(Project(p.id), {"engine": "google", "dub": True, "render": False})
        assert [x[0] for x in order] == ["translate", "dub"]
    finally:
        save_settings({"split_long_trans": True})


def test_autoflow_links_preset_prefetch(sample_video, monkeypatch):
    """Tải về & dịch tự động: nhận ra link trùng (nhiều dạng), mỗi link 1 dự án = việc tải + việc dịch theo thiết lập
    tự động dùng chung (dừng trước bước xuất); link đã tải -> dự án cũ, không tải lại; tải video kế tiếp chạy trong lúc
    việc dài khác đang chạy, việc dịch của cùng dự án chờ tải xong."""
    import shutil

    from trami import autoflow, jobs, pipeline
    from trami.config import load_settings, save_settings
    from trami.project import Project

    lk = autoflow.link_key
    assert lk("https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333&p=2") == \
        lk("https://m.bilibili.com/video/BV1xx411c7mD/?p=2") == "bili:BV1xx411c7mD:2"
    assert lk("https://www.bilibili.com/video/BV1xx411c7mD") != lk("https://www.bilibili.com/video/BV1xx411c7mD?p=2")
    assert lk("https://youtu.be/dQw4w9WgXcQ?si=a") == lk("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3") == \
        lk("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "yt:dQw4w9WgXcQ"
    assert lk("https://www.douyin.com/video/7301234567890123456") == \
        lk("https://www.douyin.com/jingxuan?modal_id=7301234567890123456") == "id:7301234567890123456"
    assert lk("https://example.com/a/b/") == lk("https://www.example.com/a/b") and lk("") == ""

    old = load_settings()
    save_settings({"auto_preset": {"dub": True, "hardsub_mode": "blur"}, "translate_engine": "google",
                   "dub_voice_all": "@auto", "split_long_trans": True})
    try:
        o = autoflow.preset()
        assert o["dub"] and o["hardsub_mode"] == "blur" and o["method"] == "asr" and o["render"] is False
        assert o["voice"] == "@auto" and o["engine"] == "google" and autoflow.preset(render=True)["render"]
        sm = autoflow.summary()
        assert "tách câu dài" in sm and "tự gán nam / nữ" in sm and "lồng tiếng" in sm

        subs, seen = [], []
        monkeypatch.setattr(jobs.manager, "submit", lambda kind, title, fn, pid="", gpu=None: subs.append(
            (kind, fn, pid)))
        monkeypatch.setattr(jobs.manager, "list", lambda project_id=None: [])

        def fake_dl(url, dest, progress=None, cancel=None, notes=None, settings=None):
            dest.mkdir(parents=True, exist_ok=True)
            return Path(shutil.copy(sample_video, dest / "Phim hay.mp4"))

        monkeypatch.setattr(pipeline, "download_video", fake_dl)
        monkeypatch.setattr(pipeline, "auto", lambda p, op, *a, **k: seen.append((p.id, dict(op))) or {"message": "ok"})
        J = type("J", (), {"update": staticmethod(lambda *a: None), "cancelled": staticmethod(lambda: False)})()
        u1 = "https://www.bilibili.com/video/BV1aa411c7mD"
        u2 = "https://www.douyin.com/video/7301234567890123499"
        res = autoflow.queue_links([u1, u2, "https://m.bilibili.com/video/BV1aa411c7mD?share=1"])
        assert [r["new"] for r in res] == [True, True] and [k for k, _, _ in subs] == ["download", "auto"] * 2
        assert subs[0][2] == subs[1][2] == res[0]["pid"]
        for _, fn, _ in subs:
            fn(J)
        p1 = Project(res[0]["pid"])
        assert p1.data["source"] and p1.data["name"] == "Phim hay" and p1.data["link_key"] == "bili:BV1aa411c7mD:1"
        assert [pid for pid, _ in seen] == [res[0]["pid"], res[1]["pid"]] and seen[0][1]["render"] is False
        assert seen[0][1]["dub"] and seen[0][1]["voice"] == "@auto"
        # dán lại link (dạng khác): không tạo dự án / không tải lại; chưa có phụ đề -> dịch tiếp, có rồi -> chỉ mở
        subs.clear()
        res2 = autoflow.queue_links(["https://www.bilibili.com/video/BV1aa411c7mD/?vd_source=x"])
        assert res2[0]["pid"] == res[0]["pid"] and not res2[0]["new"] and [k for k, _, _ in subs] == ["auto"]
        p1.segments = [Segment(0, 1, "你好", "", "S1")]
        p1.save()
        subs.clear()
        res3 = autoflow.queue_links([u1], auto=True)
        assert not res3[0]["new"] and not res3[0]["queued"] and subs == []
        res4 = autoflow.queue_links(["https://www.youtube.com/watch?v=abcdefghijk"], auto=False)
        assert [k for k, _, _ in subs] == ["download"] and res4[0]["new"]
        # việc dịch tự động của dự án mà tải không được -> bỏ qua có lời giải thích
        with pytest.raises(RuntimeError, match="tải video không được"):
            autoflow._auto(res4[0]["pid"], o)(J)
    finally:
        save_settings({"auto_preset": old.get("auto_preset", {}), "translate_engine": old.get("translate_engine"),
                       "dub_voice_all": old.get("dub_voice_all", "")})

    # hàng đợi (chạy lần lượt): tải video không chờ việc dài, mỗi lúc 1 lượt tải, việc dịch cùng dự án chờ tải xong
    m = jobs.JobManager.__new__(jobs.JobManager)
    mk = lambda i, kind, pid: jobs.Job(id=i, kind=kind, title=i, project_id=pid, _fn=None,  # noqa: E731
                                       gpu=kind in jobs.GPU_KINDS)
    m.jobs = {"a1": mk("a1", "auto", "p1"), "d2": mk("d2", "download", "p2"), "a2": mk("a2", "auto", "p2"),
              "d3": mk("d3", "download", "p3"), "r": mk("r", "render", "p9")}
    m.running = {"a1"}
    assert m._why_wait(m.jobs["d2"]) == "" and m._why_wait(m.jobs["a2"])
    m.running = {"a1", "d2"}
    assert "lượt tải" in m._why_wait(m.jobs["d3"]) and m._why_wait(m.jobs["a2"])
    m.running = {"d2"}
    assert "dự án" in m._why_wait(m.jobs["a2"])      # cùng dự án: dịch chờ tải xong
    assert m._why_wait(m.jobs["r"]) == "" and m._why_wait(m.jobs["a1"]) == ""   # đang tải không chặn việc khác
    # việc tải của dự án đang chờ lượt -> việc dịch của dự án đó không được chạy trước (giữ thứ tự trong dự án)
    m.queue, m.running = ["d2", "a2", "d3"], {"d9"}
    m.jobs["d9"] = mk("d9", "download", "p8")
    assert "trước của dự án" in m._why_wait(m.jobs["a2"]) and "lượt tải" in m._why_wait(m.jobs["d2"])


def test_gui_home_links_and_auto_settings(sample_video, monkeypatch):
    """Trang chủ: dán nhiều link -> 'Tải về & dịch tự động' / 'Chỉ tải về'; hộp Thiết lập tự động lưu 1 bộ dùng chung,
    tab Tự động của dự án đang mở + dòng tóm tắt theo luôn; thẻ dự án có trạng thái."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from trami import autoflow
    from trami.config import load_settings, save_settings
    from trami.gui import main as gm
    from trami.gui.auto_settings import AutoSettingsDialog
    from trami.jobs import manager

    app = QApplication.instance() or QApplication([])
    old = load_settings()
    win = gm.MainWindow()
    try:
        subs = []
        monkeypatch.setattr(manager, "submit", lambda kind, title, fn, pid="", gpu=None: subs.append((kind, pid)))
        home = win.home
        home.url.setPlainText("【phim】 https://www.bilibili.com/video/BV1bb411c7mD 复制链接\n"
                              "https://www.douyin.com/video/7301234567890123477\n\nhttps://b23.tv/BV1bb411c7mD")
        assert len(home._links()) == 3
        home.download(True)
        kinds = [k for k, _ in subs]
        # link thứ 3 cùng video với link 1 -> 2 dự án
        assert kinds.count("download") == 2 and kinds.count("auto") == 2 and home.url.toPlainText() == ""
        subs.clear()
        home.url.setPlainText("https://www.douyin.com/video/7301234567890123477")
        home.download(False)
        assert subs == []      # đã có dự án của link này -> mở dự án cũ, không tải lại
        assert win.pages.currentWidget() is win.project_page

        dlg = AutoSettingsDialog(win)
        dlg.dub.setChecked(True)
        set_ok = [i for i in range(dlg.hardsub.count()) if dlg.hardsub.itemData(i) == "blur"]
        dlg.hardsub.setCurrentIndex(set_ok[0])
        dlg._save()
        st = load_settings()
        assert st["auto_preset"]["dub"] is True and st["auto_preset"]["hardsub_mode"] == "blur"
        win.preset_saved()
        assert "lồng tiếng" in home.preset_lbl.text() and "không lồng tiếng" not in home.preset_lbl.text()
        pg = win.project_page
        assert pg.a_dub.isChecked() and pg.a_hardsub.currentData() == "blur"
        pg.a_dub.setChecked(False)      # sửa ở tab Tự động -> cùng 1 bộ thiết lập
        assert load_settings()["auto_preset"]["dub"] is False and autoflow.preset()["dub"] is False

        pr = {"id": "x", "info": {"duration": 3}, "n_segments": 5, "n_translated": 5, "outputs": []}
        assert gm.project_badge(pr, [{"project_id": "x", "status": "running", "kind": "download"}])[0] == "Đang tải…"
        assert gm.project_badge(pr, [{"project_id": "x", "status": "queued", "kind": "auto"}])[0] == "Đang chờ"
        assert gm.project_badge(pr, [{"project_id": "x", "status": "error", "kind": "auto", "finished": 1}])[0] == \
            "⚠ Lỗi"
        assert gm.project_badge(pr, [])[0] == "Chờ kiểm tra & xuất"
        assert gm.project_badge({**pr, "outputs": [{"path": "a.mp4"}]}, [])[0] == "✓ Đã xuất"
    finally:
        save_settings({"auto_preset": old.get("auto_preset", {})})
        win.close()
        app.processEvents()


def test_ai_steps_run_with_live_drafts(monkeypatch):
    """Vừa tách vừa dịch: câu đã dịch nháp lúc đang nghe vẫn được AI sửa lỗi nghe (câu đổi chữ gốc -> dịch lại) + AI kiểm
    tra cả phim; chạy lại chỉ kiểm tra câu vừa dịch; bước nào chạy mà không đổi gì cũng ghi vào thông báo."""
    from trami import autoflow, pipeline, translate as tr
    from trami.config import load_settings
    from trami.project import Project

    calls = []

    def fake_chat(self, system, user, max_tokens=8000, temperature=0.3):
        kind = ("clean" if "biên tập viên phụ đề tiếng Trung" in system else "review" if "kiểm tra bản dịch" in system
                else "profile" if 'lập "HỒ SƠ PHIM"' in system else "translate")
        payload = json.loads(user[user.index("{"):]) if "{" in user else {}
        calls.append((kind, payload))
        if kind == "clean":
            return json.dumps({"fix": {"1": "真的假的"}, "merge": [], "unsure": {}}, ensure_ascii=False)
        if kind == "profile":
            return json.dumps({"the_loai": "hài"}, ensure_ascii=False)
        if kind == "review":
            return json.dumps({}, ensure_ascii=False)
        return json.dumps({k: f"Câu {k}" for k in payload}, ensure_ascii=False)

    monkeypatch.setattr(tr.LLMClient, "chat", fake_chat)
    st_ = {**load_settings(), "translate_engine": "openai", "openai_api_key": "x",
           "openai_base_url": "https://api.example.com/v1", "ai_clean_before_translate": True,
           "ai_review_after_translate": True, "split_long_trans": True, "translate_skill": ""}
    p = Project.create("live_ai")
    p.segments = [Segment(0, 1.5, "真的加的", "Nháp 1"), Segment(2, 3.5, "你好", "Nháp 2"),
                  Segment(4, 5.5, "走吧", "Nháp 3")]
    p.save()
    pipeline.translate(p, "openai", "auto", True, st_, split=True, fresh=True)
    kinds = [k for k, _ in calls]
    assert kinds[0] == "clean" and "review" in kinds          # trước đây bị bỏ qua vì câu đã có bản dịch nháp
    segs = p.segments
    assert segs[0].text == "真的假的" and segs[0].trans.startswith("Câu")   # đổi chữ gốc -> dịch lại
    assert [s.trans for s in segs[1:]] == ["Nháp 2", "Nháp 3"]             # câu khác giữ bản dịch nháp
    rev = [pl for k, pl in calls if k == "review"]
    assert sum(len(pl) for pl in rev) == 3                                  # kiểm tra cả phim
    note = p.data["skill_note"]
    assert "AI kiểm tra 3 câu dịch: không có câu cần sửa" in note and "tách câu dài: không có câu" in note
    # chạy lại (đã kiểm tra rồi, không có câu mới) -> không sửa lỗi nghe / không gửi lại cả phim
    calls.clear()
    pipeline.translate(p, "openai", "auto", True, st_, split=False)
    assert "clean" not in [k for k, _ in calls] and "review" not in [k for k, _ in calls]
    segs = p.segments
    segs[2].trans = ""
    p.segments = segs
    p.save()
    calls.clear()
    pipeline.translate(p, "openai", "auto", True, st_, split=False)
    rev = [pl for k, pl in calls if k == "review"]
    assert len(rev) == 1 and len(rev[0]) == 1                               # chỉ câu vừa dịch
    sm = autoflow.summary(st_)
    assert "AI sửa lỗi nghe, kiểm tra bản dịch" in sm and "lọc lời hát" not in sm
    sm = autoflow.summary({**st_, "ai_clean_before_translate": False})
    assert "AI lọc lời hát, kiểm tra bản dịch" in sm
    assert "AI " not in autoflow.summary({**st_, "translate_engine": "google"})
