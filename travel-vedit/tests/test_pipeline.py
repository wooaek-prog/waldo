"""합성 영상으로 로컬 단계와 엔진 동작을 검증한다.  실행: python -m pytest -q"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap

import pytest

from vedit import engines, pipeline
from vedit.project import Project, read_json, write_json
from vedit.stages import REGISTRY

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg 필요")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="module")
def footage(tmp_path_factory):
    src = tmp_path_factory.mktemp("raw")
    # 클립1: 패턴 4초 → 컬러바 4초 (장면 전환 1회), 소리 있음, GPS/촬영시각 태그
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=4",
        "-f", "lavfi", "-i", "smptebars=size=1280x720:rate=30:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=500:duration=8",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-metadata", "creation_time=2026-10-03T01:20:00Z", "-metadata", "location=+34.9949+135.7850/",
        str(src / "IMG_0001.mov"),
    )
    # 클립2: 어두운 화면 3초, 무음 (dark 플래그와 silence 확인용)
    _ffmpeg(
        "-f", "lavfi", "-i", "color=c=0x080808:size=1280x720:rate=30:duration=3",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-metadata", "creation_time=2026-10-03T03:00:00Z",
        str(src / "IMG_0002.mp4"),
    )
    return src


@pytest.fixture
def project(tmp_path, footage):
    return Project.init("trip", [str(footage)], projects_dir=tmp_path, timezone="Asia/Tokyo", duration=10)


def _local(project, *ids):
    for sid in ids:
        REGISTRY[sid](project, {"id": sid})


def test_local_preprocessing(project):
    _local(project, "ingest", "proxy", "scenes", "quality", "keyframes", "silence", "palette", "bundle")

    media = project.media()
    assert media["clip_count"] == 2
    c1, c2 = media["clips"]
    assert c1["id"] == "C0001" and c1["gps"] == {"lat": 34.9949, "lon": 135.785}
    assert c1["created"].startswith("2026-10-03T10:20")  # UTC → Asia/Tokyo
    assert (project.path("proxy/C0001.mp4")).exists() and (project.path("audio/C0002.wav")).exists()

    scenes = read_json(project.path("scenes.json"))
    assert len(scenes["C0001"]) == 2, scenes["C0001"]
    assert abs(scenes["C0001"][1]["start"] - 4.0) < 0.2

    quality = read_json(project.path("quality.json"))
    assert "dark" in quality["C0002"][0]["flags"]

    silence = read_json(project.path("silence.json"))
    assert silence["C0002"]["silent_ratio"] > 0.9 and silence["C0001"]["silent_ratio"] < 0.1

    assert len(list(project.path("keyframes").glob("*.jpg"))) == 3
    assert len(read_json(project.path("palette.json"))["colors"]) == 6
    bundle = read_json(project.path("analysis_input.json"))
    assert bundle["clips"][0]["scenes"][0]["keyframe"].startswith("keyframes/")


def _fake_cli(tmp_path, outputs):
    """호출될 때마다 outputs 를 차례로 출력하는 가짜 AI CLI."""
    script = tmp_path / "fake_ai.py"
    counter = tmp_path / "count"
    script.write_text(textwrap.dedent(f"""
        import json, pathlib, sys
        sys.stdin.read()
        c = pathlib.Path({str(counter)!r})
        n = int(c.read_text()) if c.exists() else 0
        c.write_text(str(n + 1))
        outs = {json.dumps(outputs)!r}
        print(json.loads(outs)[min(n, len(json.loads(outs)) - 1)])
    """))
    return [sys.executable, str(script)], counter


def test_cli_engine_retries_until_schema_valid(project, tmp_path):
    _local(project, "ingest")
    valid = {"title": "t", "chapters": [], "timeline": [{"clip": "C0001", "in": 0, "out": 3, "role": "hook"}]}
    cmd, counter = _fake_cli(tmp_path, [
        "죄송합니다, 다음과 같습니다: {\"title\": 1}",               # 스키마 오류
        "```json\n" + json.dumps({"result": "x"}) + "\n```",       # 필수 필드 없음
        "결과입니다\n```json\n" + json.dumps(valid) + "\n```",      # 정상
    ])
    project.engines["fake"] = {"cmd": cmd, "retries": 3, "timeout": 30}
    stage = {"id": "edl", "prompt": "prompts/story_edl.md", "in": ["project.yaml"],
             "schema": "schemas/edl.schema.json", "out": "edl.json"}
    engines.run_engine(project, stage, "fake")
    assert read_json(project.path("edl.json")) == valid
    assert counter.read_text() == "3"


def test_manual_engine_roundtrip(project):
    stage = {"id": "design", "prompt": "prompts/design_guide.md", "in": ["project.yaml", "brand/brand_guide.json"],
             "schema": "schemas/design_guide.schema.json", "out": "design/design_guide.json"}
    with pytest.raises(engines.WaitingManual):
        engines.run_engine(project, stage, "manual")
    prompt = project.path("manual/design.prompt.md").read_text(encoding="utf-8")
    assert "Pretendard Black" in prompt and "JSON Schema" in prompt

    project.path("manual/design.response.json").write_text("{\"theme\": \"x\"}", encoding="utf-8")
    with pytest.raises(engines.EngineError, match="스키마"):
        engines.run_engine(project, stage, "manual")


def test_snap_and_edl_check(project):
    _local(project, "ingest", "proxy", "scenes")
    write_json(project.path("transcript.json"), {"C0001": {"segments": [{"start": 0.4, "end": 6.9, "text": "안녕"}]}})
    write_json(project.path("analysis.json"), {
        "trip_summary": "", "mood_tags": [], "places": [],
        "segments": [
            {"clip": "C0001", "start": 0.9, "end": 7.6, "desc": "", "highlight": 0.8, "role": "talk", "place": "P01"},
            {"clip": "C0001", "start": 3.5, "end": 4.6, "desc": "", "highlight": 0.5, "role": "broll", "place": "P01"},
            {"clip": "C9999", "start": 0, "end": 2, "desc": "", "highlight": 0.1, "role": "broll", "place": "P01"},
        ],
    })
    _local(project, "snap")
    segs = read_json(project.path("analysis_snapped.json"))["segments"]
    assert len(segs) == 2
    assert segs[0]["start"] == 0.4 and segs[0]["end"] == 6.9   # 발화 경계로 맞춤
    assert abs(segs[1]["start"] - 4.0) < 0.2                   # 장면 경계로 맞춤

    write_json(project.path("edl.json"), {"title": "t", "chapters": [], "timeline": [
        {"clip": "C0001", "in": 0, "out": 20, "role": "talk"},
    ]})
    with pytest.raises(RuntimeError, match="EDL 오류"):
        _local(project, "edl_check")


def test_pipeline_resume_and_approval(project, monkeypatch):
    calls = []
    monkeypatch.setitem(REGISTRY, "ingest", lambda p, s: calls.append("ingest"))
    monkeypatch.setitem(REGISTRY, "proxy", lambda p, s: calls.append("proxy"))
    (project.dir / "pipeline.yaml").write_text(
        "stages:\n  - {id: ingest, engine: local, approve: true}\n  - {id: proxy, engine: local}\n", encoding="utf-8"
    )
    assert pipeline.run(project) is False            # 승인 대기에서 멈춤
    assert pipeline.run(project) is False            # 승인 전에는 진행 안 함
    pipeline.approve(project, "ingest")
    assert pipeline.run(project) is True
    assert calls == ["ingest", "proxy"]              # 완료 단계는 다시 실행하지 않음
    assert pipeline.reset(project, "ingest") == ["ingest", "proxy"]
