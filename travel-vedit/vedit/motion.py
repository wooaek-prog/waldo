"""모션 그래픽 렌더러: HTML/CSS/SVG 템플릿 → 투명 배경 영상(ProRes 4444).

헤드리스 크롬(Playwright)에서 템플릿 애니메이션을 멈춰 두고 프레임마다 시간을 옮겨 캡처한다.
실시간 재생 녹화가 아니라서 느린 CPU 에서도 프레임이 빠지지 않는다.

이 모듈의 공개 함수(list_templates, check, stills, render, composite)는 입력→파일/딕셔너리만 다룬다.
명령줄(vedit motion …)과 이후 로컬 서버 API 가 같은 함수를 그대로 호출한다.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from vedit.media import ffprobe, has_encoder, require
from vedit.project import CONFIG_DIR, ROOT, load_yaml

MOTION_DIR = ROOT / "motion"
TEMPLATES_DIR = MOTION_DIR / "templates"
BASE_W, BASE_H = 1920, 1080
META_RE = re.compile(r'<script[^>]*id="vedit-meta"[^>]*>(.*?)</script>', re.S)

Log = Callable[[str], None]


class TemplateError(RuntimeError):
    pass


# ---------------------------------------------------------------- 템플릿 정보
def template_path(name: str | Path) -> Path:
    p = Path(name)
    if p.suffix == ".html" and p.exists():
        return p.resolve()
    p = TEMPLATES_DIR / f"{name}.html"
    if not p.exists():
        names = ", ".join(t["id"] for t in list_templates())
        raise TemplateError(f"템플릿이 없습니다: {name} (가능: {names})")
    return p


def read_meta(path: Path) -> dict:
    m = META_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        raise TemplateError(f"{path.name}: <script id=\"vedit-meta\"> 가 없습니다")
    try:
        meta = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise TemplateError(f"{path.name}: vedit-meta JSON 오류: {e}") from e
    missing = [k for k in ("id", "name", "duration", "params") if k not in meta]
    if missing:
        raise TemplateError(f"{path.name}: vedit-meta 에 {missing} 없음")
    return meta


def list_templates() -> list[dict]:
    return [{**read_meta(p), "path": str(p)} for p in sorted(TEMPLATES_DIR.glob("*.html"))]


def _dig(data: dict, dotted: str):
    for key in dotted.split("."):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def resolve_params(meta: dict, params: dict | None = None, design_guide: dict | None = None) -> dict:
    """기본값 ← 디자인 가이드(from 연결) ← 직접 지정값 순으로 덮어쓴다."""
    specs = meta["params"]
    unknown = sorted(set(params or {}) - set(specs) - {"duration"})
    if unknown:
        raise TemplateError(f"{meta['id']}: 알 수 없는 파라미터 {unknown} (가능: {sorted(specs)})")
    out = {k: s.get("default") for k, s in specs.items()}
    if design_guide:
        for k, s in specs.items():
            val = _dig(design_guide, s["from"]) if s.get("from") else None
            if val not in (None, ""):
                out[k] = val
    out.update(params or {})
    for k, s in specs.items():
        if s.get("type") == "number" and out.get(k) is not None:
            out[k] = float(out[k])
    out.setdefault("duration", float(meta["duration"]))
    return out


def _motion_config() -> dict:
    return load_yaml(CONFIG_DIR / "local.yaml").get("motion", {}) or {}


# ---------------------------------------------------------------- 브라우저 세션
class Page:
    """템플릿 하나를 연 브라우저 페이지. seek → screenshot 을 반복한다."""

    def __init__(self, page, meta: dict, params: dict, errors: list[str], scale: float = 1.0):
        self._page = page
        self.meta = meta
        self.params = params
        self.errors = errors
        self.duration = float(page.evaluate("window.__vedit_duration"))
        self.hold = float(page.evaluate("window.__vedit_hold"))
        # 크롬 개발자 프로토콜로 직접 캡처: optimizeForSpeed 로 PNG 압축을 가볍게 해 약 2배 빠름
        try:
            self._cdp = page.context.new_cdp_session(page)
            self._cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
            # 새 CDP 세션은 Playwright 의 배율 설정을 모르므로 직접 지정 (안 하면 4K 렌더가 1080p 로 나옴)
            self._cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": BASE_W, "height": BASE_H, "deviceScaleFactor": scale, "mobile": False,
            })
        except Exception:  # noqa: BLE001 — CDP 를 못 쓰면 일반 스크린샷으로
            self._cdp = None

    def seek(self, t: float) -> None:
        self._page.evaluate("t => window.__vedit_seek(t)", t)

    def screenshot(self) -> bytes:
        if self._cdp is not None:
            data = self._cdp.send("Page.captureScreenshot", {
                "format": "png", "optimizeForSpeed": True, "captureBeyondViewport": False,
            })["data"]
            return base64.b64decode(data)
        return self._page.screenshot(omit_background=True, type="png")

    def layout_issues(self) -> list[str]:
        self.seek(self.hold)
        return list(self._page.evaluate("window.__vedit_check()"))


@contextmanager
def browser() -> Iterator[Callable[..., Page]]:
    """브라우저를 한 번 띄워 여러 템플릿을 연다 (렌더 여러 개를 묶을 때 시작 비용 절약)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise TemplateError("playwright 가 없습니다: pip install -r requirements.txt && python -m playwright install chromium") from e

    cfg = _motion_config()
    with sync_playwright() as pw:
        launch = {"executable_path": cfg["chromium"]} if cfg.get("chromium") else {}
        chromium = pw.chromium.launch(**launch)
        contexts = []

        def open_page(template: str | Path, params: dict | None = None, scale: float = 1.0,
                      design_guide: dict | None = None) -> Page:
            path = template_path(template)
            meta = read_meta(path)
            resolved = resolve_params(meta, params, design_guide)
            ctx = chromium.new_context(viewport={"width": BASE_W, "height": BASE_H}, device_scale_factor=scale)
            contexts.append(ctx)
            # 외부 네트워크 차단: 템플릿(특히 AI 가 만든 것)은 로컬 파일만 쓸 수 있다
            ctx.route("**/*", lambda route: route.continue_()
                      if route.request.url.startswith(("file:", "data:", "blob:")) else route.abort())
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(f"스크립트 오류: {e}"))
            page.on("console", lambda m: errors.append(f"콘솔 오류: {m.text}") if m.type == "error" else None)
            page.add_init_script(f"window.__VEDIT_RENDER = true; window.PARAMS = {json.dumps(resolved, ensure_ascii=False)};")
            page.goto(path.as_uri())
            try:
                page.wait_for_function("window.__vedit_ready === true || !!window.__vedit_error", timeout=20_000)
            except Exception as e:  # noqa: BLE001 — playwright TimeoutError
                raise TemplateError(f"{meta['id']}: 준비되지 않음 (runtime.js 포함 여부 확인) {errors[:3]}") from e
            err = page.evaluate("window.__vedit_error || null")
            if err:
                raise TemplateError(f"{meta['id']}: 초기화 실패: {err}")
            return Page(page, meta, resolved, errors, scale)

        try:
            yield open_page
        finally:
            for ctx in contexts:
                ctx.close()
            chromium.close()


# ---------------------------------------------------------------- 공개 기능
def check(template: str | Path, params: dict | None = None, design_guide: dict | None = None) -> dict:
    """템플릿 규약 점검: 메타, 스크립트 오류, 외부 요청, 글자 넘침/안전 영역, 실제로 움직이는지."""
    with browser() as open_page:
        page = open_page(template, params, scale=0.25, design_guide=design_guide)
        issues = page.layout_issues()
        frames = []
        for t in (0.0, page.hold, page.duration):
            page.seek(t)
            frames.append(page.screenshot())
        warnings = list(issues)
        if frames[0] == frames[1]:
            warnings.append("시작 프레임과 정지 구간 프레임이 같습니다 (등장 애니메이션 없음?)")
        if frames[1] == frames[2]:
            warnings.append("정지 구간과 마지막 프레임이 같습니다 (퇴장 애니메이션 없음?)")
        # 영상 위에 얹는 그래픽은 시작·끝 프레임이 투명해야 컷 경계에서 튀지 않는다
        for label, png in (("시작", frames[0]), ("마지막", frames[2])):
            if _alpha_ratio(png) > 0.01:
                warnings.append(f"{label} 프레임이 투명하지 않습니다 "
                                "(퇴장 애니메이션에 fill-mode: both 를 쓰면 시작부터 적용됩니다 → forwards 사용)")
        return {"template": page.meta["id"], "duration": page.duration, "hold": page.hold,
                "errors": list(page.errors), "warnings": warnings, "params": page.params}


def _alpha_ratio(png: bytes) -> float:
    """PNG 의 평균 불투명도 (0=완전 투명, 1=완전 불투명)."""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim < 3 or img.shape[2] < 4:
        return 1.0
    return float(img[..., 3].mean()) / 255.0


def stills(template: str | Path, out_dir: Path, times: list[float] | None = None, params: dict | None = None,
           scale: float = 0.5, design_guide: dict | None = None, prefix: str | None = None) -> list[Path]:
    """승인/미리보기용 정지 이미지 (기본: 정지 구간 1장)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with browser() as open_page:
        page = open_page(template, params, scale=scale, design_guide=design_guide)
        for t in times or [page.hold]:
            page.seek(t)
            p = out_dir / f"{prefix or page.meta['id']}_{t:05.2f}s.png"
            p.write_bytes(page.screenshot())
            paths.append(p)
    return paths


# 코덱별 (확장자, 컨테이너, ffmpeg 인자)
#   vp9        : 파이프라인 중간 파일 기본값. 알파 포함, 1080p 5초 ≈ 2~3MB (ProRes 의 1/50)
#   prores4444 : DaVinci/Final Cut/Premiere 로 넘길 때. 1080p 5초 ≈ 100~400MB
#   png        : 무손실 확인용
CODECS = {
    "vp9": (".webm", "webm", ["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "20", "-b:v", "0",
                              "-deadline", "realtime", "-cpu-used", "8", "-row-mt", "1", "-auto-alt-ref", "0"]),
    "prores4444": (".mov", "mov", ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                                   "-vendor", "apl0"]),
    "png": (".mov", "mov", ["-c:v", "png", "-pix_fmt", "rgba"]),
}


def default_codec() -> str:
    return _motion_config().get("codec") or "vp9"


def output_suffix(codec: str | None = None) -> str:
    return CODECS[codec or default_codec()][0]


def input_args(path: Path) -> list[str]:
    """알파가 있는 VP9 는 libvpx 디코더로 열어야 투명도가 살아난다."""
    return (["-c:v", "libvpx-vp9"] if Path(path).suffix == ".webm" else []) + ["-i", str(path)]


def render_page(page: Page, out: Path, fps: float = 30, codec: str | None = None, log: Log = print) -> dict:
    """열린 페이지를 프레임 단위로 캡처해 ffmpeg 로 바로 인코딩한다 (중간 PNG 파일 없음)."""
    require("ffmpeg", "brew install ffmpeg")
    codec = codec or default_codec()
    if codec not in CODECS:
        raise TemplateError(f"알 수 없는 코덱: {codec} (가능: {', '.join(CODECS)})")
    suffix, fmt, args = CODECS[codec]
    if out.suffix != suffix:
        out = out.with_suffix(suffix)
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.stem + ".part" + suffix)
    n = max(1, round(page.duration * fps))
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "image2pipe", "-c:v", "png", "-framerate", str(fps), "-i", "-",
           *args, "-f", fmt, str(part)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        for i in range(n):
            page.seek(i / fps)
            proc.stdin.write(page.screenshot())
            if i and i % max(1, n // 4) == 0:
                log(f"    {page.meta['id']}: {i}/{n} 프레임")
        proc.stdin.close()
        stderr = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg 인코딩 실패: {stderr[-500:]}")
    except BaseException:
        proc.kill()
        part.unlink(missing_ok=True)
        raise
    part.replace(out)
    if page.errors:
        raise TemplateError(f"{page.meta['id']}: 렌더 중 오류 {page.errors[:3]}")
    return {"path": str(out), "template": page.meta["id"], "codec": codec, "frames": n, "fps": fps,
            "duration": round(n / fps, 3), "seconds_taken": round(time.monotonic() - started, 1)}


def render(template: str | Path, out: Path, params: dict | None = None, fps: float = 30, scale: float = 1.0,
           design_guide: dict | None = None, codec: str | None = None, log: Log = print) -> dict:
    with browser() as open_page:
        page = open_page(template, params, scale=scale, design_guide=design_guide)
        warnings = page.layout_issues()
        result = render_page(page, out, fps=fps, codec=codec, log=log)
    return {**result, "warnings": warnings}


def composite(base: Path, overlays: list[tuple[Path, float]], out: Path, log: Log = print) -> Path:
    """영상 위에 투명 모션들을 지정 시각(초)에 얹는다. 미리보기와 이후 render 단계에서 공용."""
    info = ffprobe(base)
    video = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w, h = video["width"], video["height"]
    has_audio = any(s.get("codec_type") == "audio" for s in info["streams"])

    inputs = ["-i", str(base)]
    chains, prev = [], "0:v"
    for k, (path, at) in enumerate(overlays, 1):
        inputs += input_args(path)
        chains.append(f"[{k}:v]scale={w}:{h},setpts=PTS-STARTPTS+{at:.3f}/TB[o{k}]")
        chains.append(f"[{prev}][o{k}]overlay=eof_action=pass:format=auto[v{k}]")
        prev = f"v{k}"
    chains.append(f"[{prev}]format=yuv420p[vout]")
    vcodec = (["-c:v", "h264_videotoolbox", "-b:v", "12M"] if has_encoder("h264_videotoolbox")
              else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"])
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *inputs,
           "-filter_complex", ";".join(chains), "-map", "[vout]",
           *(["-map", "0:a", "-c:a", "copy"] if has_audio else []), *vcodec, str(out)]
    log(f"  합성: {base.name} + 모션 {len(overlays)}개 → {out.name}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"합성 실패: {proc.stderr[-800:]}")
    return out


def preview_url(template: str | Path, params: dict | None = None) -> str:
    """브라우저(사파리/크롬)나 앱 웹뷰에서 바로 여는 미리보기 주소. 파라미터는 #params= 로 전달."""
    from urllib.parse import quote

    path = template_path(template)
    return path.as_uri() + ("#params=" + quote(json.dumps(params, ensure_ascii=False)) if params else "")
