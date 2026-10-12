"""1단계: 원본 폴더를 훑어 클립 목록(media.json)을 만든다. 원본은 읽기만 한다.

촬영 시각/GPS 는 ffprobe 태그에서 읽는다.
- 아이폰: com.apple.quicktime.creationdate (시간대 포함), com.apple.quicktime.location.ISO6709
- 갤럭시/기타: creation_time (UTC), location / location-eng
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from vedit.media import ffprobe, hdr_kind
from vedit.project import Project, write_json

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".mkv", ".avi", ".insv"}
ISO6709 = re.compile(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")


def parse_gps(tags: dict) -> dict | None:
    for key in ("com.apple.quicktime.location.iso6709", "location", "location-eng"):
        if key in tags:
            m = ISO6709.match(tags[key].strip())
            if m:
                lat, lon = float(m.group(1)), float(m.group(2))
                if not (lat == 0 and lon == 0):
                    return {"lat": round(lat, 6), "lon": round(lon, 6)}
    return None


def parse_created(tags: dict, path: Path, tz: ZoneInfo) -> tuple[datetime, str]:
    for key in ("com.apple.quicktime.creationdate", "creation_time"):
        if key in tags:
            try:
                dt = datetime.fromisoformat(tags[key].strip().replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if dt.year > 1971:
                    return dt.astimezone(tz), key
            except ValueError:
                pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz), "file_mtime"


def _fps(stream: dict) -> float:
    num, _, den = stream.get("avg_frame_rate", "0/1").partition("/")
    try:
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []):
        if "rotation" in sd:
            return int(sd["rotation"])
    return int(stream.get("tags", {}).get("rotate", 0))


def scan(source_dirs: list[str]) -> list[Path]:
    files = []
    for d in source_dirs:
        root = Path(d)
        if not root.exists():
            raise RuntimeError(f"원본 폴더가 없습니다: {root}")
        files += [
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in VIDEO_EXT and not p.name.startswith(".")
        ]
    return sorted(set(files))


def run(project: Project, stage: dict) -> None:
    tz = ZoneInfo(project.config.get("timezone", "Asia/Seoul"))
    files = scan(project.config["source_dirs"])
    if not files:
        raise RuntimeError("원본 폴더에서 영상 파일을 찾지 못했습니다")

    clips = []
    for path in files:
        try:
            info = ffprobe(path)
        except Exception as e:  # 깨진 파일은 건너뛰고 기록만
            project.log(f"  건너뜀 (ffprobe 실패): {path.name}: {e}")
            continue
        video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
        if video is None:
            continue
        tags = {k.lower(): v for k, v in info.get("format", {}).get("tags", {}).items()}
        created, created_src = parse_created(tags, path, tz)
        clips.append({
            "path": str(path),
            "file": path.name,
            "duration": round(float(info["format"].get("duration", 0)), 3),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": _fps(video),
            "codec": video.get("codec_name"),
            "pix_fmt": video.get("pix_fmt"),
            "hdr": hdr_kind(video),  # "HLG" / "PQ" / None — 프록시·렌더에서 SDR 톤매핑
            "rotation": _rotation(video),
            "has_audio": any(s.get("codec_type") == "audio" for s in info["streams"]),
            "created": created.isoformat(timespec="seconds"),
            "created_source": created_src,
            "gps": parse_gps(tags),
            "device": tags.get("com.apple.quicktime.model") or tags.get("com.android.manufacturer"),
        })

    clips.sort(key=lambda c: (c["created"], c["file"]))
    days: dict[str, list[str]] = {}
    for i, c in enumerate(clips, 1):
        c["id"] = f"C{i:04d}"
        c["day"] = c["created"][:10]
        days.setdefault(c["day"], []).append(c["id"])

    total = sum(c["duration"] for c in clips)
    write_json(project.path("media.json"), {
        "clip_count": len(clips),
        "total_duration": round(total, 1),
        "days": days,
        "clips": clips,
    })
    hdr = sum(1 for c in clips if c["hdr"])
    if hdr:
        project.log(f"  HDR 클립 {hdr}개 → SDR(BT.709)로 톤매핑해서 처리")
    no_time = sum(c["created_source"] == "file_mtime" for c in clips)
    project.log(f"  클립 {len(clips)}개, 총 {total / 60:.1f}분, {len(days)}일치"
                + (f" (촬영시각 태그 없음 {no_time}개 → 파일 수정시각 사용)" if no_time else ""))
