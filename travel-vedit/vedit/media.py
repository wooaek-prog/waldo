"""ffmpeg / ffprobe 호출 헬퍼."""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
from pathlib import Path


class ToolMissing(RuntimeError):
    pass


def require(binary: str, hint: str = "") -> str:
    path = shutil.which(binary)
    if not path:
        raise ToolMissing(f"'{binary}' 를 찾을 수 없습니다. {hint}".strip())
    return path


def ffprobe(path: Path) -> dict:
    require("ffprobe", "brew install ffmpeg")
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def run_ffmpeg(args: list[str], capture: bool = False) -> subprocess.CompletedProcess:
    require("ffmpeg", "brew install ffmpeg")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg 실패: {' '.join(cmd)}\n{tail}")
    return proc


@functools.cache
def encoders() -> str:
    require("ffmpeg", "brew install ffmpeg")
    return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


def has_encoder(name: str) -> bool:
    return any(line.split()[1:2] == [name] for line in encoders().splitlines() if line.strip())
