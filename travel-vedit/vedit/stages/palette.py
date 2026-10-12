"""대표 색 추출(palette.json)과 디자인 가이드 승인용 무드보드(design/moodboard.html)."""

from __future__ import annotations

import html
import json

import cv2
import numpy as np

from vedit.project import Project, read_json, write_json


def run(project: Project, stage: dict) -> None:
    k = int(project.local.get("palette", {}).get("colors", 6))
    frames = sorted(project.path("keyframes").glob("*.jpg"))
    if not frames:
        raise RuntimeError("keyframes/ 가 비어 있습니다 (keyframes 단계 먼저)")
    pixels = []
    for f in frames:
        img = cv2.imread(str(f))
        if img is not None:
            pixels.append(cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA).reshape(-1, 3))
    data = np.concatenate(pixels).astype(np.float32)
    k = min(k, len(data))
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _, labels, centers = cv2.kmeans(data, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k)
    colors = []
    for idx in np.argsort(-counts):
        b, g, r = (int(round(v)) for v in centers[idx])
        colors.append({"hex": f"#{r:02X}{g:02X}{b:02X}", "ratio": round(float(counts[idx] / counts.sum()), 3)})
    write_json(project.path("palette.json"), {"source_frames": len(frames), "colors": colors})
    project.log("  대표 색: " + " ".join(c["hex"] for c in colors))


def moodboard(project: Project, stage: dict) -> None:
    """design_guide.json 을 한 장짜리 HTML 로 렌더해서 사람이 승인하기 쉽게 한다."""
    guide = read_json(project.path("design/design_guide.json"))
    palette = read_json(project.path("palette.json"))
    frames = sorted(project.path("keyframes").glob("*.jpg"))
    step = max(1, len(frames) // 12)
    thumbs = "".join(f'<img src="../keyframes/{f.name}" alt="">' for f in frames[::step][:12])

    def swatches(items):
        return "".join(
            f'<div class="sw"><span style="background:{html.escape(hexv)}"></span><code>{html.escape(name)}<br>{html.escape(hexv)}</code></div>'
            for name, hexv in items
        )

    guide_colors = [(k, v) for k, v in guide.get("palette", {}).items()]
    footage_colors = [(f"{c['ratio'] * 100:.0f}%", c["hex"]) for c in palette["colors"]]
    pal = guide.get("palette", {})
    fonts = guide.get("fonts", {})
    page = f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>{html.escape(guide.get('theme', '디자인 가이드'))}</title>
<style>
body{{margin:0;font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;background:#111;color:#eee;padding:32px}}
h1{{margin:0 0 4px}} h2{{margin-top:36px;font-size:16px;color:#aaa;font-weight:600}}
.row{{display:flex;flex-wrap:wrap;gap:12px}} .sw span{{display:block;width:96px;height:64px;border-radius:8px}}
.sw code{{font-size:12px;color:#bbb}} .grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}}
.grid img{{width:100%;border-radius:6px}}
.title{{position:relative;aspect-ratio:16/9;max-width:720px;border-radius:10px;overflow:hidden;
  background:linear-gradient(135deg,{pal.get('primary', '#333')},{pal.get('secondary', pal.get('accent', '#666'))})}}
.title b{{position:absolute;left:5%;bottom:12%;font-size:56px;color:{pal.get('text', '#fff')};
  font-family:'{html.escape(fonts.get('title', 'sans-serif'))}',sans-serif}}
.title i{{position:absolute;left:5%;bottom:6%;font-style:normal;color:{pal.get('accent', '#fc0')};font-size:22px}}
pre{{background:#1b1b1b;padding:16px;border-radius:8px;overflow:auto;font-size:12px}}
</style>
<h1>{html.escape(guide.get('theme', ''))}</h1><div>{html.escape(project.config.get('title', ''))}</div>
<h2>가이드 팔레트</h2><div class="row">{swatches(guide_colors)}</div>
<h2>원본 영상 대표 색</h2><div class="row">{swatches(footage_colors)}</div>
<h2>타이틀 카드 미리보기 (대략적)</h2>
<div class="title"><b>{html.escape(guide.get('thumbnail', {}).get('text', project.config.get('title', '')))}</b>
<i>{html.escape(guide.get('location_title', {}).get('style', ''))}</i></div>
<h2>대표 장면</h2><div class="grid">{thumbs}</div>
<h2>전체 JSON</h2><pre>{html.escape(json.dumps(guide, ensure_ascii=False, indent=2))}</pre>
</html>"""
    out = project.path("design/moodboard.html")
    out.write_text(page, encoding="utf-8")
    project.log(f"  무드보드: {out}  (브라우저로 열어 확인: open '{out}')")
