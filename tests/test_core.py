"""Kiểm thử tự động: chạy bằng `python -m pytest -q tests`."""
import json
import os
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
        out = {k: f"VI({v})" for k, v in payload.items()}
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
