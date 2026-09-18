#!/usr/bin/env python3
"""학교 운동장(옥외체육장) 수광점 – 분석지점도 격자 재현.

종전에는 운동장을 8m 균등격자로 채웠다. 실제 교육환경평가 분석지점도는
운동장을 **정해진 칸 수로 나누고 칸마다 한 점**을 찍는다(여의도초 8×8 = 64점).
칸 수가 다르면 충족률의 분모가 달라지므로 도면대로 맞춘다.

경계 좌표를 받은 학교는 그 폴리곤을 쓰고, 아직 못 받은 학교는 옥외지반 안에
들어가는 **최대 내접 사각형**(블록 장축 정렬)에 격자를 얹는다 — 격자 수·방위는
도면대로지만 위치·크기는 추정이라 근거등급 C 로 남는다.

경계를 배치도 래스터에서 계측한 경우(`boundary: "raster"`, `school_site_map.py`
참조)는 근거등급 A− 다 — 위치는 도면대로지만 화소 계측 오차 ±3m 가 남는다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.prepared import prep

import hwarang_massing_study as H

DEFAULT_SPEC = Path(__file__).with_name("data") / "school_playgrounds.json"
GROUND_Z = 0.0


def load_spec(path: Path = DEFAULT_SPEC) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def inscribed_rect(open_area, azimuth: float, aspect: float,
                   step: float = 2.0) -> Polygon | None:
    """azimuth 에 정렬한 최대 내접 직사각형(장변:단변 = aspect).

    개방지 안에 완전히 들어가는 사각형 중 가장 큰 것을 격자 탐색으로 찾는다.
    """
    if open_area.is_empty:
        return None
    centre = open_area.centroid
    rot = affinity.rotate(open_area, -(90.0 - azimuth), origin=centre)
    minx, miny, maxx, maxy = rot.bounds
    nx = int((maxx - minx) // step) + 1
    ny = int((maxy - miny) // step) + 1
    ready = prep(rot)
    occ = [[ready.contains(Point(minx + ix * step + step / 2,
                                 miny + iy * step + step / 2))
            for ix in range(nx)] for iy in range(ny)]

    # 각 열의 연속 개방 높이를 누적하며 aspect 를 만족하는 최대 사각형 탐색
    best = None
    heights = [0] * nx
    for iy in range(ny):
        for ix in range(nx):
            heights[ix] = heights[ix] + 1 if occ[iy][ix] else 0
        for ix in range(nx):
            if not heights[ix]:
                continue
            hmin = heights[ix]
            for jx in range(ix, nx):
                hmin = min(hmin, heights[jx])
                if not hmin:
                    break
                w = (jx - ix + 1) * step
                hgt = hmin * step
                long_m, short_m = max(w, hgt), min(w, hgt)
                if abs(long_m / short_m - aspect) > 0.12 * aspect:
                    continue
                if best is None or w * hgt > best[0]:
                    best = (w * hgt, ix, jx, iy - hmin + 1, iy)
    if best is None:
        return None
    _a, x0, x1, y0, y1 = best
    rect = box(minx + x0 * step, miny + y0 * step,
               minx + (x1 + 1) * step, miny + (y1 + 1) * step)
    return affinity.rotate(rect, (90.0 - azimuth), origin=centre)


def grid_points(rect: Polygon, grid: Sequence[int], azimuth: float,
                ) -> list[tuple[int, int, float, float]]:
    """사각형을 grid[0]×grid[1] 칸으로 나눈 각 칸의 중앙점. (i, j, x, y)."""
    centre = rect.centroid
    rot = affinity.rotate(rect, -(90.0 - azimuth), origin=centre)
    minx, miny, maxx, maxy = rot.bounds
    ni, nj = int(grid[0]), int(grid[1])
    dx, dy = (maxx - minx) / ni, (maxy - miny) / nj
    out = []
    for i in range(ni):
        for j in range(nj):
            p = affinity.rotate(
                Point(minx + (i + 0.5) * dx, miny + (j + 0.5) * dy),
                (90.0 - azimuth), origin=centre)
            out.append((i + 1, j + 1, p.x, p.y))
    return out


def build(jibun: str, open_area, spec_all: dict[str, Any], label: str,
          fallback_step: float = 8.0):
    """운동장 수광점. 반환 (폴리곤, [Receptor], 근거설명, 격자표기 여부).

    도면 정의가 없으면 종전 방식(개방지 전체를 fallback_step 격자로)으로
    돌아간다.
    """
    entry = spec_all.get(jibun)
    if not entry:
        pts = []
        minx, miny, maxx, maxy = open_area.bounds
        ready = prep(open_area)
        y = miny + fallback_step / 2
        while y <= maxy:
            x = minx + fallback_step / 2
            while x <= maxx:
                if ready.contains(Point(x, y)):
                    pts.append(H.Receptor(x, y, GROUND_Z, 180.0, label, 0, True))
                x += fallback_step
            y += fallback_step
        return open_area, pts, "옥외지반 근사(격자 미정)", False

    grid = entry["grid"]
    az = float(entry.get("azimuth", 52.0))
    if entry.get("polygon"):
        rect = Polygon(entry["polygon"])
        basis = ("배치도 경계(래스터 계측) + 분석지점도 격자"
                 if entry.get("boundary") == "raster"
                 else "도면 경계 + 분석지점도 격자")
    else:
        aspect = float(entry.get("aspect") or (max(grid) / min(grid)))
        rect = inscribed_rect(open_area, az, aspect)
        if rect is None:
            rect = open_area
        basis = "분석지점도 격자(경계는 최대 내접 사각형으로 추정)"
    pts = [H.Receptor(x, y, GROUND_Z, 180.0, label, 0, True)
           for _i, _j, x, y in grid_points(rect, grid, az)]
    return rect, pts, basis, True


def cell_index(jibun: str, rect: Polygon, spec_all: dict[str, Any]
               ) -> list[tuple[int, int]]:
    entry = spec_all.get(jibun)
    if not entry:
        return []
    return [(i, j) for i, j, _x, _y in
            grid_points(rect, entry["grid"], float(entry.get("azimuth", 52.0)))]
