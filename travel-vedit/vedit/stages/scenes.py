"""3단계: 장면 분할(scenes.json)과 장면별 대표 프레임(keyframes/).

여행 영상은 한 컷이 길게 이어지는 경우가 많아, 너무 긴 장면은 max_scene_sec 단위로 잘라
AI 분석 단위를 고르게 만든다.
"""

from __future__ import annotations

import cv2

from vedit.project import Project, read_json, write_json


def _split_long(start: float, end: float, max_len: float) -> list[list[float]]:
    out, t = [], start
    while end - t > max_len * 1.5:
        out.append([round(t, 3), round(t + max_len, 3)])
        t += max_len
    out.append([round(t, 3), round(end, 3)])
    return out


def run(project: Project, stage: dict) -> None:
    from scenedetect import ContentDetector, detect

    cfg = project.local.get("scenes", {})
    threshold = float(cfg.get("threshold", 27))
    max_len = float(cfg.get("max_scene_sec", 15))
    min_len = float(cfg.get("min_scene_sec", 1.0))

    result = {}
    for clip in project.media()["clips"]:
        proxy = project.path(f"proxy/{clip['id']}.mp4")
        found = detect(str(proxy), ContentDetector(threshold=threshold, min_scene_len=int(min_len * max(clip["fps"], 1))))
        sec = lambda tc: tc.seconds if hasattr(type(tc), "seconds") else tc.get_seconds()  # noqa: E731
        bounds = [(sec(s), sec(e)) for s, e in found] or [(0.0, clip["duration"])]
        scenes = []
        for s, e in bounds:
            scenes += _split_long(s, e, max_len)
        result[clip["id"]] = [{"idx": i, "start": s, "end": e} for i, (s, e) in enumerate(scenes)]
    write_json(project.path("scenes.json"), result)
    project.log(f"  장면 {sum(len(v) for v in result.values())}개")


def keyframes(project: Project, stage: dict) -> None:
    width = int(project.local.get("keyframes", {}).get("width", 480))
    scenes = read_json(project.path("scenes.json"))
    kdir = project.path("keyframes")
    kdir.mkdir(exist_ok=True)
    index = {}
    for clip_id, items in scenes.items():
        cap = cv2.VideoCapture(str(project.path(f"proxy/{clip_id}.mp4")))
        for sc in items:
            name = f"{clip_id}_s{sc['idx']:03d}.jpg"
            out = kdir / name
            if not out.exists():
                cap.set(cv2.CAP_PROP_POS_MSEC, (sc["start"] + sc["end"]) / 2 * 1000)
                ok, frame = cap.read()
                if not ok:
                    continue
                h, w = frame.shape[:2]
                frame = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(out), frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            index[f"{clip_id}:{sc['idx']}"] = f"keyframes/{name}"
        cap.release()
    write_json(project.path("keyframes.json"), index)
    project.log(f"  대표 프레임 {len(index)}장")
