"""AI 분석 결과의 타임코드 보정 (analysis.json → analysis_snapped.json).

LLM 이 준 시작/끝 시각은 수 초씩 틀릴 수 있으므로, 가까운 장면 경계와 발화 경계로 맞춘다.
발화 중간에서 잘리지 않도록 발화 경계를 우선한다.
"""

from __future__ import annotations

from vedit.project import Project, read_json, write_json


def _nearest(t: float, candidates: list[float], tol: float) -> float:
    best = min(candidates, key=lambda c: abs(c - t), default=t)
    return best if abs(best - t) <= tol else t


def run(project: Project, stage: dict) -> None:
    tol = float(project.local.get("snap", {}).get("tolerance_sec", 1.5))
    analysis = read_json(project.path("analysis.json"))
    scenes = read_json(project.path("scenes.json"))
    tpath = project.path("transcript.json")
    transcript = read_json(tpath) if tpath.exists() else {}
    durations = {c["id"]: c["duration"] for c in project.media()["clips"]}

    kept, dropped = [], 0
    for seg in analysis["segments"]:
        cid = seg["clip"]
        if cid not in durations:
            dropped += 1
            continue
        scene_b = sorted({b for s in scenes.get(cid, []) for b in (s["start"], s["end"])})
        speech = transcript.get(cid, {}).get("segments", [])
        speech_starts = [s["start"] for s in speech]
        speech_ends = [s["end"] for s in speech]

        # 시작은 끝보다 충분히 앞, 끝은 시작보다 충분히 뒤의 경계로만 맞춘다 (구간이 0 으로 접히지 않게)
        limit = seg["end"] - 0.5
        start = _nearest(seg["start"], [x for x in speech_starts if x < limit], tol)
        if start == seg["start"]:
            start = _nearest(seg["start"], [x for x in scene_b if x < limit], tol)
        limit = start + 0.5
        end = _nearest(seg["end"], [x for x in speech_ends if x > limit], tol)
        if end == seg["end"]:
            end = _nearest(seg["end"], [x for x in scene_b if x > limit], tol)
        start, end = max(0.0, start), min(durations[cid], end)
        if end - start < 0.5:
            dropped += 1
            continue
        kept.append({**seg, "start": round(start, 3), "end": round(end, 3),
                     "orig": [seg["start"], seg["end"]]})

    write_json(project.path("analysis_snapped.json"), {**analysis, "segments": kept, "snapped": True})
    project.log(f"  타임코드 보정: {len(kept)}개 유지, {dropped}개 제외(없는 클립/너무 짧음)")
