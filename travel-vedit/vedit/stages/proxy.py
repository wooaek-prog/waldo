"""2단계: 분석·미리보기용 720p 프록시와 STT 용 16kHz 모노 WAV 생성.

iMac 2019 는 4K HEVC 10bit 원본을 직접 다루면 느리므로 이후 모든 분석은 프록시로 한다.
하드웨어 인코더(h264_videotoolbox)가 있으면 사용하고 없으면 libx264 로 대체.
"""

from __future__ import annotations

from vedit.media import has_encoder, run_ffmpeg
from vedit.project import Project


def _video_codec_args(height: int) -> list[str]:
    if has_encoder("h264_videotoolbox"):
        return ["-c:v", "h264_videotoolbox", "-b:v", "3M" if height >= 720 else "1.5M"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26"]


def run(project: Project, stage: dict) -> None:
    height = int(stage.get("height", project.local.get("proxy", {}).get("height", 720)))
    media = project.media()
    pdir, adir = project.path("proxy"), project.path("audio")
    pdir.mkdir(exist_ok=True)
    adir.mkdir(exist_ok=True)

    for i, clip in enumerate(media["clips"], 1):
        out = pdir / f"{clip['id']}.mp4"
        if not out.exists():
            project.log(f"  프록시 {i}/{len(media['clips'])} {clip['file']}")
            part = out.with_suffix(".part")
            # 세로 영상도 짧은 변 기준이 아니라 높이 기준으로 축소 (회전은 ffmpeg 가 자동 적용)
            run_ffmpeg([
                "-i", clip["path"],
                "-vf", f"scale=-2:'min({height},ih)',format=yuv420p",
                *_video_codec_args(height),
                "-c:a", "aac", "-b:a", "96k", "-ac", "2",
                "-movflags", "+faststart", "-f", "mp4", str(part),
            ])
            part.replace(out)

        wav = adir / f"{clip['id']}.wav"
        if clip["has_audio"] and not wav.exists():
            part = wav.with_suffix(".part")
            run_ffmpeg(["-i", clip["path"], "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(part)])
            part.replace(wav)
