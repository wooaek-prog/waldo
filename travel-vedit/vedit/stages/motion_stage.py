"""모션 그래픽 단계.

motion_context : EDL 타임라인 + 템플릿 목록을 AI 가 보기 좋게 압축 (motion_context.json)
motion         : motion_spec.json(AI 가 정한 배치·문구)을 투명 영상으로 렌더 (motion/*.mov, *.png, manifest.json)
                 색·폰트는 design/design_guide.json 에서 자동 적용. 같은 입력이면 다시 렌더하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from vedit import motion
from vedit.project import Project, read_json, write_json


def context(project: Project, stage: dict) -> None:
    edl = read_json(project.path("edl.json"))
    clips = {c["id"]: c for c in project.media()["clips"]}
    chapters = {ch["id"]: ch for ch in edl.get("chapters", [])}
    timeline, t = [], 0.0
    for i, item in enumerate(edl["timeline"]):
        length = (item["out"] - item["in"]) / float(item.get("speed") or 1)
        ch = chapters.get(item.get("chapter") or "")
        timeline.append({
            "index": i, "start_sec": round(t, 2), "length": round(length, 2), "role": item["role"],
            "clip": item["clip"], "day": clips.get(item["clip"], {}).get("day"),
            "chapter": item.get("chapter"), "chapter_title": ch["title"] if ch else None,
            "caption": item.get("caption"),
        })
        t += length
    templates = [
        {"id": m["id"], "name": m["name"], "category": m.get("category"), "description": m.get("description"),
         "duration": m["duration"],
         # 색·폰트(from 연결)는 디자인 가이드에서 자동 적용되므로 AI 에게는 문구 파라미터만 보여 준다
         "params": {k: {"label": s.get("label"), "default": s.get("default")}
                    for k, s in m["params"].items() if not s.get("from") and k != "duration"}}
        for m in motion.list_templates()
    ]
    write_json(project.path("motion_context.json"), {
        "title": project.config.get("title"),
        "days": sorted({x["day"] for x in timeline if x["day"]}),
        "total_sec": round(t, 1),
        "templates": templates,
        "timeline": timeline,
    })
    project.log(f"  모션 문맥: 컷 {len(timeline)}개, 템플릿 {len(templates)}종")


def _target(project: Project) -> tuple[float, float]:
    cfg = project.local.get("motion", {}) or {}
    target = project.config.get("target", {})
    width = int(str(target.get("resolution", "1920x1080")).split("x")[0])
    scale = float(cfg.get("scale") or width / motion.BASE_W)
    return scale, float(target.get("fps", 30))


def _render_job(job: dict) -> dict:
    """모션 하나 렌더 (별도 프로세스에서 실행될 수 있어 입력/출력은 단순 dict)."""
    out, still = Path(job["out"]), Path(job["still"])
    with motion.browser() as open_page:
        page = open_page(job["template"], job["params"], scale=job["scale"], design_guide=job["guide"])
        warnings = page.layout_issues()
        page.seek(page.hold)
        still.write_bytes(page.screenshot())
        result = motion.render_page(page, out, fps=job["fps"], codec=job["codec"], log=lambda _m: None)
        return {**result, "warnings": warnings, "category": page.meta.get("category")}


def run(project: Project, stage: dict) -> None:
    spec = read_json(project.path("motion_spec.json"))
    gpath = project.path("design/design_guide.json")
    guide = read_json(gpath) if gpath.exists() else None
    scale, fps = _target(project)
    codec = motion.default_codec()
    workers = int((project.local.get("motion", {}) or {}).get("workers", 2))
    mdir = project.path("motion")
    mdir.mkdir(exist_ok=True)
    old = {m["id"]: m for m in (read_json(mdir / "manifest.json")["overlays"] if (mdir / "manifest.json").exists() else [])}

    ids = [item["id"] for item in spec["overlays"]]
    if len(set(ids)) != len(ids):
        raise RuntimeError(f"motion_spec 의 id 가 중복됩니다: {ids}")

    manifest: dict[str, dict] = {}
    jobs = []
    for item in spec["overlays"]:
        tpath = motion.template_path(item["template"])
        resolved = motion.resolve_params(motion.read_meta(tpath), item.get("params"), guide)  # 오타 파라미터는 여기서 실패
        key = hashlib.sha1(json.dumps(
            [tpath.read_text(encoding="utf-8"), resolved, scale, fps, codec], ensure_ascii=False, sort_keys=True
        ).encode()).hexdigest()[:16]
        prev = old.get(item["id"])
        if prev and prev.get("hash") == key and project.path(prev["path"]).exists():
            manifest[item["id"]] = {**prev, **item}  # 배치(at_index/offset)만 바뀐 경우는 다시 렌더하지 않음
            continue
        out = mdir / f"{item['id']}{motion.output_suffix(codec)}"
        jobs.append({"item": item, "hash": key, "template": str(tpath), "params": item.get("params") or {},
                     "guide": guide, "scale": scale, "fps": fps, "codec": codec,
                     "out": str(out), "still": str(mdir / f"{item['id']}.png")})

    if jobs:
        project.log(f"  모션 {len(jobs)}개 렌더 (동시 {min(workers, len(jobs))}개, {scale:g}배 해상도, {codec})")
        results = None
        if workers > 1 and len(jobs) > 1:
            try:
                ctx = multiprocessing.get_context("spawn")
                with ProcessPoolExecutor(max_workers=min(workers, len(jobs)), mp_context=ctx) as pool:
                    results = list(pool.map(_render_job, jobs))
            except BrokenProcessPool as e:  # 병렬 실행이 안 되는 환경(내장 실행 등) → 순서대로
                project.log(f"  병렬 렌더 불가 ({e}) → 하나씩 렌더")
        if results is None:
            results = [_render_job(j) for j in jobs]
        for job, res in zip(jobs, results):
            item = job["item"]
            for w in res["warnings"]:
                project.log(f"  ⚠ {item['id']}: {w}")
            project.log(f"  ✓ {item['id']} {item['template']} ({res['seconds_taken']}초)")
            manifest[item["id"]] = {
                **item, "path": f"motion/{Path(res['path']).name}", "still": f"motion/{item['id']}.png",
                "duration": res["duration"], "category": res["category"], "warnings": res["warnings"],
                "hash": job["hash"],
            }

    write_json(mdir / "manifest.json", {"scale": scale, "fps": fps, "codec": codec,
                                        "overlays": [manifest[i] for i in ids]})
    project.log(f"  모션 {len(ids)}개 준비 — 확인: {mdir}/*.png")
