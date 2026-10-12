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


@functools.cache
def _filters() -> str:
    require("ffmpeg", "brew install ffmpeg")
    return subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout


def has_filter(name: str) -> bool:
    return any(line.split()[1:2] == [name] for line in _filters().splitlines() if line.strip())


# ---------------------------------------------------------------- HDR
# 아이폰(기본 설정)은 HDR(HLG, 돌비비전은 HLG 기반)로 촬영한다. SDR 로 그냥 줄이면 색이 회색빛으로 바래므로
# BT.2020 HDR → BT.709 SDR 톤매핑이 필요하다.
HDR_TRANSFER = {"arib-std-b67": "HLG", "smpte2084": "PQ"}


def hdr_kind(stream: dict) -> str | None:
    return HDR_TRANSFER.get(stream.get("color_transfer", ""))


def tonemap_filter(kind: str | None, cfg: dict | None = None) -> str | None:
    """HDR → SDR 필터 체인 (zscale 필요). SDR 이면 None."""
    if not kind:
        return None
    if not has_filter("zscale"):
        raise ToolMissing("HDR 영상 톤매핑에 zscale 필터가 필요합니다 (brew install ffmpeg 기본 빌드에 포함)")
    cfg = cfg or {}
    tin = "arib-std-b67" if kind == "HLG" else "smpte2084"
    npl = cfg.get("npl", 100)
    algo = cfg.get("tonemap", "hable")
    return (f"zscale=tin={tin}:pin=bt2020:min=bt2020nc:t=linear:npl={npl},format=gbrpf32le,"
            f"zscale=p=bt709,tonemap=tonemap={algo}:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
