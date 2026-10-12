"""AI 분석 입력 묶음(analysis_input.json).

로컬 단계 결과(메타, 장면, 화질, 자막, 무음)를 장면 단위로 압축해 하나의 JSON 으로 만든다.
AI 에는 이 파일 + keyframes/ 만 넘기므로 구독 사용량이 크게 줄어든다.
"""

from __future__ import annotations

from vedit.project import Project, read_json, write_json


def _optional(project: Project, name: str) -> dict:
    p = project.path(name)
    return read_json(p) if p.exists() else {}


def _speech(segments: list[dict], start: float, end: float) -> str:
    return " ".join(s["text"] for s in segments if s["end"] > start and s["start"] < end)[:400]


def run(project: Project, stage: dict) -> None:
    media = project.media()
    scenes = read_json(project.path("scenes.json"))
    quality = _optional(project, "quality.json")
    transcript = _optional(project, "transcript.json")
    silence = _optional(project, "silence.json")
    keyframes = _optional(project, "keyframes.json")

    clips = []
    for c in media["clips"]:
        q = {s["idx"]: s for s in quality.get(c["id"], [])}
        segs = transcript.get(c["id"], {}).get("segments", [])
        clips.append({
            "clip": c["id"],
            "day": c["day"],
            "time": c["created"][11:16],
            "gps": c["gps"],
            "duration": c["duration"],
            "orientation": "portrait" if (c["height"] or 0) > (c["width"] or 0) or abs(c["rotation"]) == 90 else "landscape",
            "silent_ratio": silence.get(c["id"], {}).get("silent_ratio"),
            "scenes": [
                {
                    "idx": s["idx"], "start": s["start"], "end": s["end"],
                    "keyframe": keyframes.get(f"{c['id']}:{s['idx']}"),
                    "flags": q.get(s["idx"], {}).get("flags", []),
                    "motion": q.get(s["idx"], {}).get("motion"),
                    "speech": _speech(segs, s["start"], s["end"]) or None,
                }
                for s in scenes.get(c["id"], [])
            ],
        })
    write_json(project.path("analysis_input.json"), {
        "trip": {
            "title": project.config.get("title"),
            "request": project.config.get("request"),
            "days": {d: len(ids) for d, ids in media["days"].items()},
            "total_footage_min": round(media["total_duration"] / 60, 1),
        },
        "clips": clips,
    })
    project.log(f"  분석 입력 묶음 생성 (클립 {len(clips)}개)")
