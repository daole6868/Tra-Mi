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
    assert len(segs) == 1 and segs[0].end == 2 and segs[0].trans == "Xin chào bạn"
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
    monkeypatch.setattr(translate.requests, "post", fake_post)
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
    pipeline.auto(p2, {"target": "th", "render": False}, {"translate_engine": "google"})
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
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)
    monkeypatch.setattr(hd, "xet_enabled", lambda: not (tmp_path / "hf_no_xet.txt").exists())
    calls = []

    def fake_run(repo, patterns, token, xet, progress, cancel, label, lo, hi):
        calls.append(xet)
        return ("stall", "không nhận thêm dữ liệu") if xet else ("ok", str(tmp_path))
    monkeypatch.setattr(hd, "_run", fake_run)
    msgs = []
    assert hd.download("a/b", progress=lambda x, m: msgs.append(m)) == tmp_path
    assert calls == [True, False] and (tmp_path / "hf_no_xet.txt").exists()
    assert any("chuyển sang tải thường" in m for m in msgs)
    assert os.environ.get("HF_HUB_DISABLE_XET") == "1"
    monkeypatch.delenv("HF_HUB_DISABLE_XET", raising=False)

    # tải thường đứng im 2 lần -> báo lỗi mạng rõ ràng; sai tên kho / cần token -> báo ngay, không thử lại
    monkeypatch.setattr(hd, "_run", lambda *a: calls.append("h") or ("stall", "đứng im"))
    calls.clear()
    with pytest.raises(RuntimeError, match="bị đứng"):
        hd.download("a/b")
    assert calls == ["h", "h"]
    monkeypatch.setattr(hd, "_run", lambda *a: calls.append("g") or ("error", "GatedRepoError: 401 Client Error"))
    calls.clear()
    with pytest.raises(RuntimeError, match="401"):
        hd.download("a/b")
    assert calls == ["g"]
