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

        def synth(self, text, out, voice=None, ref_audio=None):
            calls.append((text, ref_audio, voice))
            return tts.write_wav(out, np.sin(np.linspace(0, 800, 48000)).astype(np.float32) * 0.3, 48000)

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
    p.segments = [Segment(0, 1.0, "你好", "Xin chào bạn thân mến của tôi ơi"), Segment(2, 3, "走", "Đi thôi")]
    p.save()
    with pytest.raises(ValueError, match="AI"):
        pipeline.shorten_lines(p, [0], {"translate_engine": "google"})
    monkeypatch.setattr(translate.Translator, "shorten", lambda self, items: ["Chào bạn" for _ in items])
    n = pipeline.shorten_lines(p, [0], {"translate_engine": "openai", "openai_api_key": "x"})
    segs = p.segments
    assert n == 1 and segs[0].trans == "Chào bạn" and segs[0].extra["trans_long"].startswith("Xin chào")
    assert segs[1].trans == "Đi thôi"


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

        def synth(self, text, out, voice=None, ref_audio=None, ref_text=None):
            calls.append((text, Path(ref_audio).name if ref_audio else None, ref_text))
            return tts.write_wav(out, np.sin(np.linspace(0, 800, 24000)).astype(np.float32) * 0.3, 24000)

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
