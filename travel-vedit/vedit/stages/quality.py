"""4단계: 장면별 화질 점수 (quality.json). AI 없이 OpenCV 로 계산.

- sharpness : 라플라시안 분산 (낮을수록 흐림)
- brightness: 평균 밝기 0~255
- motion    : 프레임 간 평균 이동량(px, 320px 폭 기준) — 패닝/이동 정도
- jitter    : 이동량의 급격한 변화 — 손떨림 지표

임계값은 config/local.yaml 의 quality 항목에서 조정. 처음 몇 편은 결과를 보고 튜닝할 것.
"""

from __future__ import annotations

import cv2
import numpy as np

from vedit.project import Project, read_json, write_json

WORK_WIDTH = 320


def _analyze_clip(path: str, sample_fps: float):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, round(fps / sample_fps))
    samples, prev, prev_shift, n = [], None, None, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if n % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            gray = cv2.cvtColor(cv2.resize(frame, (WORK_WIDTH, int(h * WORK_WIDTH / w))), cv2.COLOR_BGR2GRAY)
            sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            bright = float(gray.mean())
            motion, jitter = 0.0, 0.0
            g32 = gray.astype(np.float32)
            if prev is not None and prev.shape == g32.shape:
                (dx, dy), _ = cv2.phaseCorrelate(prev, g32)
                motion = float(np.hypot(dx, dy))
                if prev_shift is not None:
                    jitter = float(np.hypot(dx - prev_shift[0], dy - prev_shift[1]))
                prev_shift = (dx, dy)
            prev = g32
            samples.append((n / fps, sharp, bright, motion, jitter))
        n += 1
    cap.release()
    return samples


def run(project: Project, stage: dict) -> None:
    cfg = project.local.get("quality", {})
    th = {
        "blurry": float(cfg.get("blurry_below", 60)),
        "dark": float(cfg.get("dark_below", 40)),
        "bright": float(cfg.get("overexposed_above", 215)),
        "shaky": float(cfg.get("shaky_above", 4.0)),
    }
    scenes = read_json(project.path("scenes.json"))
    result = {}
    for clip_id, items in scenes.items():
        samples = _analyze_clip(str(project.path(f"proxy/{clip_id}.mp4")), float(cfg.get("sample_fps", 2)))
        arr = np.array(samples) if samples else np.zeros((0, 5))
        per_scene = []
        for sc in items:
            sel = arr[(arr[:, 0] >= sc["start"]) & (arr[:, 0] < sc["end"])] if len(arr) else arr
            if len(sel) == 0:
                per_scene.append({"idx": sc["idx"], "flags": ["no_samples"]})
                continue
            sharp, bright = float(np.median(sel[:, 1])), float(sel[:, 2].mean())
            motion, jitter = float(sel[:, 3].mean()), float(sel[:, 4].mean())
            flags = []
            if sharp < th["blurry"]:
                flags.append("blurry")
            if bright < th["dark"]:
                flags.append("dark")
            if bright > th["bright"]:
                flags.append("overexposed")
            if jitter > th["shaky"]:
                flags.append("shaky")
            # 0~1 사용성 점수: 플래그 하나당 감점 (AI 와 EDL 이 참고용으로만 사용)
            usable = max(0.0, 1.0 - 0.35 * len(flags))
            per_scene.append({
                "idx": sc["idx"], "sharpness": round(sharp, 1), "brightness": round(bright, 1),
                "motion": round(motion, 2), "jitter": round(jitter, 2), "flags": flags, "usable": round(usable, 2),
            })
        result[clip_id] = per_scene
    write_json(project.path("quality.json"), result)
    bad = sum(1 for v in result.values() for s in v if s.get("flags"))
    project.log(f"  문제 장면 {bad}개 표시 (blurry/dark/overexposed/shaky)")
