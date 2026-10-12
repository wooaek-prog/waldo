"""5단계: 음성 인식(transcript.json)과 무음 구간(silence.json).

STT 는 whisper.cpp(whisper-cli) 사용. 인텔 iMac 에서는 CPU 로 돌기 때문에
large-v3-turbo 양자화 모델 + 밤 시간 일괄 처리를 권장한다.
"""

from __future__ import annotations

import json
import re
import subprocess

from vedit.media import require, run_ffmpeg
from vedit.project import ROOT, Project, write_json


def run(project: Project, stage: dict) -> None:
    cfg = project.local["whisper"]
    binary = require(cfg["bin"], "brew install whisper-cpp")
    model = ROOT / cfg["model"]
    if not model.exists():
        raise RuntimeError(f"whisper 모델이 없습니다: {model} (setup/install_mac.sh 실행)")

    tdir = project.path("transcript")
    tdir.mkdir(exist_ok=True)
    clips = [c for c in project.media()["clips"] if c["has_audio"]]
    merged = {}
    for i, clip in enumerate(clips, 1):
        base = tdir / clip["id"]
        out_json = base.with_suffix(".json")
        if not out_json.exists():
            project.log(f"  STT {i}/{len(clips)} {clip['file']} ({clip['duration'] / 60:.1f}분)")
            proc = subprocess.run([
                binary, "-m", str(model), "-f", str(project.path(f"audio/{clip['id']}.wav")),
                "-l", str(cfg.get("language", "auto")), "-t", str(cfg.get("threads", 6)),
                "-oj", "-of", str(base),
            ], capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"whisper 실패 ({clip['file']}): {proc.stderr.strip()[-400:]}")
        data = json.loads(out_json.read_text(encoding="utf-8"))
        segs = []
        for s in data.get("transcription", []):
            text = s.get("text", "").strip()
            if text and not re.fullmatch(r"[\[\(].*[\]\)]", text):  # [음악], (박수) 같은 표기는 제외
                segs.append({"start": s["offsets"]["from"] / 1000, "end": s["offsets"]["to"] / 1000, "text": text})
        merged[clip["id"]] = {"language": data.get("result", {}).get("language"), "segments": segs}
    write_json(project.path("transcript.json"), merged)
    project.log(f"  발화 구간 {sum(len(v['segments']) for v in merged.values())}개")


_SIL = re.compile(r"silence_(start|end): (-?[\d.]+)")


def silence(project: Project, stage: dict) -> None:
    cfg = project.local.get("silence", {})
    noise, dur = cfg.get("noise_db", -35), cfg.get("min_sec", 0.8)
    result = {}
    for clip in project.media()["clips"]:
        if not clip["has_audio"]:
            result[clip["id"]] = {"no_audio": True, "ranges": [[0, clip["duration"]]]}
            continue
        proc = run_ffmpeg([
            "-i", str(project.path(f"audio/{clip['id']}.wav")),
            "-af", f"silencedetect=noise={noise}dB:d={dur}", "-f", "null", "-",
        ])
        ranges, start = [], None
        for kind, val in _SIL.findall(proc.stderr):
            if kind == "start":
                start = max(0.0, float(val))
            elif start is not None:
                ranges.append([round(start, 2), round(float(val), 2)])
                start = None
        if start is not None:
            ranges.append([round(start, 2), clip["duration"]])
        silent = sum(e - s for s, e in ranges)
        result[clip["id"]] = {"ranges": ranges, "silent_ratio": round(silent / max(clip["duration"], 0.01), 2)}
    write_json(project.path("silence.json"), result)
