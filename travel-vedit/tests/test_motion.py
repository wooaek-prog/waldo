"""모션 그래픽 렌더러 검증 (헤드리스 크롬 + ffmpeg 필요).  실행: python -m pytest -q tests/test_motion.py"""

from __future__ import annotations

import os
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from vedit import motion
from vedit.project import Project, read_json, write_json
from vedit.stages import REGISTRY


def _chromium_ok() -> bool:
    try:
        with motion.browser():
            return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not _chromium_ok(), reason="ffmpeg 와 Playwright Chromium 필요"
)


def _alpha_frames(path, times):
    """VP9 알파 영상에서 지정 시각 프레임의 알파 채널 평균을 구한다."""
    out = []
    for t in times:
        png = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", *motion.input_args(path), "-ss", str(t),
             "-frames:v", "1", "-pix_fmt", "rgba", "-f", "image2pipe", "-c:v", "png", "-"],
            capture_output=True, check=True,
        ).stdout
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
        out.append(float(img[..., 3].mean()))
    return out


def test_builtin_templates_pass_check():
    ids = {t["id"] for t in motion.list_templates()}
    assert {"place_lower_third", "day_card", "opening_title"} <= ids
    for tid in ids:
        r = motion.check(tid)
        assert r["errors"] == [], (tid, r["errors"])
        assert r["warnings"] == [], (tid, r["warnings"])


def test_params_from_design_guide_and_unknown_param():
    meta = motion.read_meta(motion.template_path("place_lower_third"))
    guide = {"palette": {"accent": "#00FF00", "text": "#111111"}, "fonts": {"title": "Gmarket Sans Bold"}}
    p = motion.resolve_params(meta, {"text": "#FFFFFF"}, guide)
    assert p["accent"] == "#00FF00"           # 디자인 가이드에서
    assert p["text"] == "#FFFFFF"             # 직접 지정이 우선
    assert p["font_title"] == "Gmarket Sans Bold"
    with pytest.raises(motion.TemplateError, match="알 수 없는 파라미터"):
        motion.resolve_params(meta, {"titel": "오타"})


def test_long_title_is_shrunk_then_flagged():
    ok = motion.check("place_lower_third", {"title": "아라시야마 대나무숲"})
    assert ok["warnings"] == []               # 조금 긴 이름은 자동으로 글자 크기를 줄여 맞춤
    bad = motion.check("place_lower_third", {"title": "가" * 40})
    assert any("넘칩니다" in w for w in bad["warnings"]), bad["warnings"]


def test_render_has_transparent_start_and_visible_hold(tmp_path):
    r = motion.render("place_lower_third", tmp_path / "lt", fps=10, scale=0.25)
    assert r["path"].endswith(".webm") and r["frames"] == 45
    size = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", r["path"]], capture_output=True, text=True, check=True).stdout.strip()
    assert size == "480,270"                  # scale 이 실제 출력 해상도에 반영됨 (4K = scale 2)
    start, hold = _alpha_frames(r["path"], [0, 2.2])
    assert start < 1 and hold > 5             # 처음엔 완전 투명, 정지 구간엔 그래픽이 보임


def test_broken_template_reports_errors_and_blocks_network(tmp_path):
    (tmp_path / "templates").mkdir()
    os.symlink(motion.MOTION_DIR / "runtime", tmp_path / "runtime")
    bad = tmp_path / "templates" / "bad.html"
    bad.write_text("""<!doctype html><html><head>
<script type="application/json" id="vedit-meta">{"id":"bad","name":"bad","duration":2,"params":{}}</script>
<link rel="stylesheet" href="../runtime/runtime.css"><script src="../runtime/runtime.js"></script>
</head><body><div class="stage"><img src="https://example.com/x.png">
<script>undefinedFunction();</script></div></body></html>""", encoding="utf-8")
    r = motion.check(bad)
    joined = " ".join(r["errors"])
    assert "스크립트 오류" in joined
    assert "콘솔 오류" in joined               # 외부 이미지 요청이 차단됨


def test_composite_places_overlay_at_time(tmp_path):
    base = tmp_path / "base.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:size=480x270:rate=10:duration=6", "-pix_fmt", "yuv420p", str(base)], check=True)
    ov = motion.render("day_card", tmp_path / "day", fps=10, scale=0.25)["path"]
    out = motion.composite(base, [(ov, 2.0)], tmp_path / "out.mp4")

    def mean_at(t):
        png = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i", str(out),
                              "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
        return float(cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE).mean())

    assert mean_at(1.0) < 2                    # 모션 전: 원본(검정) 그대로
    assert mean_at(3.6) > 15                   # 모션 중: 카드가 덮음


def test_motion_stage_renders_and_caches(tmp_path):
    proj = Project.init("trip", [str(tmp_path)], projects_dir=tmp_path / "projects")
    write_json(proj.path("media.json"), {"clips": [
        {"id": "C0001", "day": "2026-10-03", "duration": 30},
        {"id": "C0002", "day": "2026-10-04", "duration": 30},
    ], "days": {}, "total_duration": 60})
    write_json(proj.path("edl.json"), {"title": "t", "chapters": [{"id": "ch1", "title": "기요미즈데라"}], "timeline": [
        {"clip": "C0001", "in": 0, "out": 5, "role": "hook"},
        {"clip": "C0001", "in": 10, "out": 18, "role": "establishing", "chapter": "ch1"},
        {"clip": "C0002", "in": 0, "out": 8, "role": "broll", "chapter": "ch1"},
    ]})
    REGISTRY["motion_context"](proj, {})
    ctx = read_json(proj.path("motion_context.json"))
    assert [x["start_sec"] for x in ctx["timeline"]] == [0, 5, 13]
    lt = next(t for t in ctx["templates"] if t["id"] == "place_lower_third")
    assert "accent" not in lt["params"] and "title" in lt["params"]   # 색·폰트는 AI 에게 숨김

    proj.local["motion"] = {"scale": 0.25, "workers": 2}
    spec = {"overlays": [
        {"id": "M01", "template": "opening_title", "at_index": 1, "offset": 0.3, "params": {"title": "교토"}},
        {"id": "M02", "template": "place_lower_third", "at_index": 1, "offset": 0.5, "params": {"title": "기요미즈데라"}},
    ]}
    write_json(proj.path("motion_spec.json"), spec)
    REGISTRY["motion"](proj, {})
    manifest = read_json(proj.path("motion/manifest.json"))
    assert [m["id"] for m in manifest["overlays"]] == ["M01", "M02"]
    files = [proj.path(m["path"]) for m in manifest["overlays"]] + [proj.path(m["still"]) for m in manifest["overlays"]]
    assert all(f.exists() for f in files)
    mtimes = [f.stat().st_mtime for f in files]

    spec["overlays"][1]["offset"] = 1.0       # 배치만 바꾸면 다시 렌더하지 않음
    write_json(proj.path("motion_spec.json"), spec)
    REGISTRY["motion"](proj, {})
    assert [f.stat().st_mtime for f in files] == mtimes
    assert read_json(proj.path("motion/manifest.json"))["overlays"][1]["offset"] == 1.0

    spec["overlays"][1]["params"] = {"titel": "오타"}
    write_json(proj.path("motion_spec.json"), spec)
    with pytest.raises(motion.TemplateError):
        REGISTRY["motion"](proj, {})
