"""render 단계 종단 검증: 합성 영상(가로/세로/무음) → 미리보기·최종본.  실행: python -m pytest -q tests/test_render.py"""

from __future__ import annotations

import re
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from vedit.project import Project, read_json, write_json
from vedit.stages import REGISTRY
from vedit.stages.render import _wrap, youtube_chapters

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 필요")


def _ff(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _probe(path, entries, stream="v:0"):
    return subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries,
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def _frame(path, t):
    png = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(path),
                          "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
    return cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)


def _integrated_lufs(path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", err)[-1])


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("render")
    raw = root / "raw"
    raw.mkdir()
    _ff("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=10", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata", "creation_time=2026-10-03T01:00:00Z",
        str(raw / "A.mp4"))
    _ff("-f", "lavfi", "-i", "smptebars=size=720x1280:rate=30:duration=8", "-f", "lavfi", "-i", "sine=frequency=660:duration=8",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata", "creation_time=2026-10-03T02:00:00Z",
        str(raw / "B.mp4"))
    _ff("-f", "lavfi", "-i", "color=c=0x3060a0:size=1280x720:rate=30:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-metadata", "creation_time=2026-10-04T01:00:00Z", str(raw / "C.mp4"))

    proj = Project.init("trip", [str(raw)], projects_dir=root / "projects", duration=15)
    proj.config["target"]["resolution"] = "640x360"
    for sid in ("ingest", "proxy"):
        REGISTRY[sid](proj, {"id": sid})

    write_json(proj.path("transcript.json"), {"C0001": {"segments": [
        {"start": 0.5, "end": 3.5, "text": "안녕하세요 드디어 교토에 도착했습니다 오늘 날씨가 정말 좋네요"}]}})
    write_json(proj.path("design/design_guide.json"), {
        "theme": "t", "palette": {"primary": "#202830", "secondary": "#405060", "accent": "#F2B880", "text": "#FFFFFF"},
        "grade": {"lut": "warm_film.cube", "saturation": 1.05, "contrast": 1.05, "warmth": 0.3},
        "fonts": {"title": "Pretendard Bold", "caption": "Pretendard SemiBold"},
        "captions": {"size": 48, "color": "#FFFFFF", "outline_color": "#000000", "outline": 3, "position": "bottom"},
        "location_title": {"style": "lower_left_slide", "duration": 3},
        "transitions": {"default": "cut", "chapter": "fade_white_0.4"},
        "music": {"moods": ["calm"], "bpm_range": [80, 100]},
        "thumbnail": {"concept": "", "text": "", "image_prompt": ""},
    })
    write_json(proj.path("edl.json"), {"title": "t", "chapters": [
        {"id": "ch1", "title": "Day 1 · 기요미즈데라"}, {"id": "ch2", "title": "Day 2 · 아라시야마"}],
        "timeline": [
            {"clip": "C0001", "in": 0, "out": 4, "role": "talk", "chapter": "ch1", "audio": "keep", "caption": "기요미즈데라 도착"},
            {"clip": "C0002", "in": 1, "out": 5, "role": "broll", "chapter": "ch1"},
            {"clip": "C0003", "in": 0, "out": 6, "role": "transit", "chapter": "ch2", "speed": 2},
            {"clip": "C0001", "in": 5, "out": 9, "role": "ending"},
        ]})
    proj.path("music").mkdir()
    _ff("-f", "lavfi", "-i", "sine=frequency=220:duration=20", str(proj.path("music/bgm.wav")))
    return proj


def test_wrap_and_chapters_rules():
    assert all(len(x) <= 10 for x in _wrap("안녕하세요 드디어 교토에 도착했습니다", 10))
    assert _wrap("띄어쓰기없는아주긴문장입니다", 6) == ["띄어쓰기없는", "아주긴문장입", "니다"]
    tl = [{"start": 0, "length": 5, "role": "hook"}, {"start": 5, "length": 40, "role": "talk", "chapter": "a"},
          {"start": 45, "length": 30, "role": "broll", "chapter": "b"}, {"start": 75, "length": 20, "role": "ending"}]
    chapters, warn = youtube_chapters(tl, {"chapters": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]})
    assert chapters[0] == (0.0, "A")          # 5초 만에 첫 챕터 → 0:00 을 그 이름으로
    assert [t for t, _ in chapters] == [0.0, 45, 75] and warn == []
    short, _ = youtube_chapters(tl[:1] + [{**tl[1], "length": 3}, {**tl[2], "start": 8}], {"chapters": []})
    assert short[0][1] == "a"                 # 짧은 챕터가 이어져도 첫 챕터 이름이 덮이지 않음


def test_long_subtitle_split_evenly():
    from vedit.stages.render import subtitle_events
    tl = [{"clip": "C1", "in": 0, "out": 10, "start": 0, "length": 10, "speed": 1, "audio_mode": "keep"}]
    text = "안녕하세요 드디어 교토에 도착했습니다 오늘 날씨가 정말 좋네요"
    ev = subtitle_events(tl, {"C1": {"segments": [{"start": 0, "end": 6, "text": text}]}}, 18)
    durs = [e - s for s, e, _ in ev]
    assert len(ev) == 2 and min(durs) > 2.0, ev       # 짧은 꼬리 없이 비슷한 길이로
    assert all(len(line) <= 18 for _, _, t in ev for line in t.split("\n"))


def test_render_preview_and_final(project):
    # 모션 오버레이: 반투명 빨간 상자(VP9 알파)를 2번째 컷 시작 0.5초 뒤에 얹는다 → 4.5~6.5초
    project.path("motion").mkdir(exist_ok=True)
    _ff("-f", "lavfi", "-i", "color=c=red@0.9:size=320x180:rate=30:duration=2,format=yuva420p",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0", str(project.path("motion/M01.webm")))
    write_json(project.path("motion/manifest.json"), {"overlays": [
        {"id": "M01", "template": "box", "at_index": 1, "offset": 0.5, "path": "motion/M01.webm"}]})

    REGISTRY["render_preview"](project, {})
    prev = project.path("output/preview.mp4")
    assert prev.exists()
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(prev)],
                               capture_output=True, text=True).stdout)
    assert abs(dur - 15) < 0.3, dur            # 4 + 4 + 6/2 + 4
    assert _probe(prev, "stream=width,height") == "640,360"
    assert _probe(prev, "stream=codec_type", stream="a:0") == "audio"

    report = read_json(project.path("output/render_report_preview.json"))
    assert report["bgm"] and report["subtitles"] >= 2 and report["on_screen_captions"] == 1
    assert [o["at"] for o in report["overlays"]] == [4.5]
    assert report["warnings"] == ["유튜브 챕터는 3개 이상이어야 표시됩니다 (현재 1개)"]  # 15초 영상이라 당연

    srt = project.path("output/subtitles.srt").read_text(encoding="utf-8")
    assert "-->" in srt and all(len(line) <= 18 for line in srt.splitlines() if line and "-->" not in line and not line.isdigit())
    chapters = project.path("output/chapters.txt").read_text(encoding="utf-8").splitlines()
    assert chapters[0].startswith("00:00 ")

    # 세로 영상(4~8초 구간): 좌우가 검은 띠가 아니라 흐린 배경으로 채워짐
    f = _frame(prev, 6.0)
    assert f[:, :40].mean() > 25
    # 모션: 5.5초엔 빨간 상자가 화면을 덮고, 7초엔 사라짐
    b, g, r = _frame(prev, 5.5).reshape(-1, 3).mean(axis=0)
    assert r > 150 and g < 90, (b, g, r)
    b, g, r = _frame(prev, 7.0).reshape(-1, 3).mean(axis=0)
    assert not (r > 150 and g < 90)
    # 챕터 전환(8초, fade_white): 시작 순간은 하얗게
    assert _frame(prev, 8.02).mean() > 180
    # 음량 정규화 (-14 LUFS 근처)
    assert abs(_integrated_lufs(prev) - (-14)) < 2.5

    REGISTRY["render"](project, {})
    final = project.path("output/trip_final.mp4")
    assert final.exists() and _probe(final, "stream=width,height") == "640,360"
    segs = sorted(project.path("render/final").glob("seg_*.mov"))
    mtimes = [s.stat().st_mtime for s in segs]
    REGISTRY["render"](project, {})            # 같은 설정이면 컷 재인코딩 없이 마무리만 다시
    assert [s.stat().st_mtime for s in sorted(project.path("render/final").glob("seg_*.mov"))] == mtimes


def test_package(project):
    write_json(project.path("youtube_meta.json"), {
        "titles": ["교토 3박4일", "교토 단풍", "교토 브이로그"], "description": "교토 여행 기록입니다.",
        "tags": ["교토", "kyoto"], "hashtags": ["#교토여행"], "pinned_comment": "어디가 제일 좋았나요?"})
    if not project.path("output/chapters.txt").exists():
        project.path("output").mkdir(exist_ok=True)
        project.path("output/chapters.txt").write_text("00:00 오프닝\n", encoding="utf-8")
    REGISTRY["package"](project, {})
    text = project.path("output/youtube.txt").read_text(encoding="utf-8")
    assert "교토 여행 기록입니다." in text and "00:00" in text and "#교토여행" in text


def test_hdr_clip_is_tonemapped(tmp_path):
    from vedit.media import has_encoder, has_filter
    if not (has_encoder("libx265") and has_filter("zscale")):
        pytest.skip("libx265 / zscale 필요")
    raw = tmp_path / "raw"
    raw.mkdir()
    # 아이폰 HDR 과 같은 표기(HLG, BT.2020, 10bit HEVC)의 합성 영상
    _ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3", "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
        "-x265-params", "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error",
        "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", str(raw / "hdr.mov"))
    proj = Project.init("hdr", [str(raw)], projects_dir=tmp_path / "projects", duration=3)
    proj.config["target"]["resolution"] = "640x360"
    for sid in ("ingest", "proxy"):
        REGISTRY[sid](proj, {"id": sid})
    assert proj.media()["clips"][0]["hdr"] == "HLG"
    proxy = proj.path("proxy/C0001.mp4")
    assert _probe(proxy, "stream=pix_fmt,color_transfer") in ("yuv420p,bt709", "yuv420p")
    write_json(proj.path("edl.json"), {"title": "t", "chapters": [], "timeline": [
        {"clip": "C0001", "in": 0, "out": 2.5, "role": "broll"}]})
    REGISTRY["render"](proj, {})
    assert proj.path("output/hdr_final.mp4").exists()
    assert _frame(proj.path("output/hdr_final.mp4"), 1.0).mean() > 40   # 톤매핑 후에도 정상 밝기
