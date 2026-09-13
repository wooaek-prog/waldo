#!/usr/bin/env python3
"""여의도 화랑아파트 재건축 – 주변 학교 일조영향 최소화 배치안 도출.

GIS건물통합정보(AL_D010)와 대교아파트 신축 폴리곤을 조합하여,
준주거 용적률 400% / 건폐율 60% 범위에서 주변 학교 교실 창면의
동지일 일조시간을 최대로 확보하는 배치안을 정량 비교한다.

우선순위: ① 일조권 충족 → ② 층수 고층화 → ③ 최대 용적률 → ④ 한강뷰
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely import wkb
from shapely.affinity import rotate, scale, translate
from shapely.geometry import MultiPoint, Point, Polygon, mapping
from shapely.ops import unary_union
from shapely.prepared import prep

from daegyo_site_polygons import compass_name, solar_position

# --------------------------------------------------------------------------- #
# 전제값
# --------------------------------------------------------------------------- #
GROUND_ELEV_M = 13.3           # 여의도 계획 지반고 (평탄지 가정)
SCHOOL_FLOOR_H = 3.5           # 학교 교사동 층고
SCHOOL_WINDOW_H = 1.2          # 창 중심 높이(해당 층 바닥 기준)
RESI_FLOOR_H = 2.95            # 공동주택 층고
ROOFTOP_M = 4.0                # 옥탑/파라펫

USE_FLOOR_HEIGHT = {
    "공동주택": 2.8,            # 기존 구축 아파트
    "교육연구시설": 3.5,
    "업무시설": 3.9,
    "판매시설": 4.5,
    "제1종근린생활시설": 4.0,
    "제2종근린생활시설": 4.0,
    "근린생활시설": 4.0,
    "운동시설": 5.0,
    "노유자시설": 3.3,
    "종교시설": 4.5,
    "문화및집회시설": 4.5,
    "의료시설": 3.6,
    "숙박시설": 3.3,
    "방송통신시설": 3.9,
    "창고시설": 4.0,
}
DEFAULT_FLOOR_HEIGHT = 3.3

HWARANG_JIBUN = "40-4"
SCHOOL_USE = "교육연구시설"


# --------------------------------------------------------------------------- #
# 자료구조
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Prism:
    """수직 각기둥 차폐물 (평지붕, 지반면에서 top_m 높이)."""

    footprint: Polygon
    top_m: float
    label: str


@dataclass(frozen=True)
class Receptor:
    """학교 교실 창면(수직) 또는 운동장 지반(수평) 수광점."""

    x: float
    y: float
    z: float               # 지반면 기준 높이
    normal_az: float       # 창면 바깥 방향 방위각
    building: str
    floor: int
    horizontal: bool = False   # True면 수평면(운동장) – 창면 방위 제약 없음


@dataclass
class Scheme:
    """화랑아파트 재건축 배치안."""

    code: str
    name: str
    towers: list[Prism] = field(default_factory=list)
    description: str = ""

    @property
    def footprint_m2(self) -> float:
        return sum(t.footprint.area for t in self.towers)

    @property
    def max_floors(self) -> int:
        return max(self.floors_of(t) for t in self.towers) if self.towers else 0

    @staticmethod
    def floors_of(tower: Prism) -> int:
        return int(round((tower.top_m - ROOFTOP_M) / RESI_FLOOR_H))

    @property
    def gfa_m2(self) -> float:
        return sum(t.footprint.area * self.floors_of(t) for t in self.towers)

    @property
    def mean_home_elev_m(self) -> float:
        """세대 평균 높이 – 한강 조망 지표(높을수록 유리)."""
        total_area = weighted = 0.0
        for tower in self.towers:
            floors = self.floors_of(tower)
            for i in range(1, floors + 1):
                weighted += tower.footprint.area * ((i - 0.5) * RESI_FLOOR_H)
                total_area += tower.footprint.area
        return weighted / total_area if total_area else 0.0


# --------------------------------------------------------------------------- #
# 입력 처리
# --------------------------------------------------------------------------- #
def read_gpkg_geom(blob: bytes) -> Any:
    envelope = (blob[3] >> 1) & 0x07
    offset = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[envelope]
    return wkb.loads(blob[offset:])


def load_buildings(path: Path) -> list[dict[str, Any]]:
    """AL_D010(GIS건물통합정보) GeoPackage를 읽는다."""
    conn = sqlite3.connect(path)
    table = conn.execute(
        "select table_name from gpkg_contents where data_type='features'"
    ).fetchone()[0]
    rows: list[dict[str, Any]] = []
    for blob, jibun, use, name, floors, area in conn.execute(
        f'select geom, A5, A9, A25, A26, A12 from "{table}"'
    ):
        if blob is None:
            continue
        geom = read_gpkg_geom(blob)
        if geom.is_empty or geom.area < 1.0:
            continue
        rows.append({
            "geom": geom,
            "jibun": jibun or "",
            "use": use or "",
            "name": name or "",
            "floors": int(floors or 0),
            "build_area": float(area or 0.0),
        })
    return rows


def load_daegyo(path: Path) -> list[Prism]:
    """대교아파트 신축 폴리곤(GeoJSON 또는 GeoPackage)을 차폐물로 읽는다."""
    prisms: list[Prism] = []
    if path.suffix.lower() == ".gpkg":
        conn = sqlite3.connect(path)
        table = conn.execute(
            "select table_name from gpkg_contents where data_type='features'"
        ).fetchone()[0]
        query = f'select geom, id, height_m from "{table}"'
        for blob, fid, height in conn.execute(query):
            prisms.append(Prism(read_gpkg_geom(blob), float(height), f"대교 {fid}"))
        return prisms
    with path.open(encoding="utf-8") as fp:
        data = json.load(fp)
    for feature in data["features"]:
        props = feature["properties"]
        geom = Polygon(feature["geometry"]["coordinates"][0])
        prisms.append(Prism(geom, float(props["height_m"]), f"대교 {props['id']}"))
    return prisms


def building_height(row: dict[str, Any]) -> float:
    floors = row["floors"]
    if floors <= 0:
        return 0.0
    return floors * USE_FLOOR_HEIGHT.get(row["use"], DEFAULT_FLOOR_HEIGHT) + 2.0


# --------------------------------------------------------------------------- #
# 대상지 · 수광점
# --------------------------------------------------------------------------- #
def build_site(buildings: Sequence[dict[str, Any]], site_area_m2: float) -> Polygon:
    """화랑아파트 기존 3개동을 감싸는 최소회전사각형을 인가 대지면적으로 확대."""
    parts = [b["geom"] for b in buildings if b["jibun"] == HWARANG_JIBUN]
    if not parts:
        raise SystemExit("화랑아파트(여의도동 40-4) 건물을 찾지 못했습니다.")
    rect = unary_union(parts).minimum_rotated_rectangle
    factor = math.sqrt(site_area_m2 / rect.area)
    return scale(rect, factor, factor, origin="centroid")


def site_axes(site: Polygon) -> tuple[float, float]:
    """대지 장변 방위각과 장변 길이를 구한다."""
    coords = list(site.exterior.coords)
    best_len, best_az = 0.0, 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length > best_len:
            best_len = length
            best_az = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180
    return best_az, best_len


def make_receptors(
    schools: Sequence[dict[str, Any]], step_m: float = 5.0
) -> list[Receptor]:
    """학교 외벽을 따라 층별 교실 창면 수광점을 생성한다.

    겨울철 일조가 가능한 남향 계열(동향~남향~서향) 창면만 대상으로 한다.
    """
    receptors: list[Receptor] = []
    for row in schools:
        geom = row["geom"]
        polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        floors = max(1, row["floors"])
        label = f"{row['jibun']}"
        for polygon in polygons:
            boundary = polygon.exterior
            n_steps = max(4, int(boundary.length // step_m))
            for k in range(n_steps):
                distance = (k + 0.5) * boundary.length / n_steps
                point = boundary.interpolate(distance)
                ahead = boundary.interpolate(
                    (distance + 0.5) % boundary.length
                )
                # 외벽 접선의 법선 중 폴리곤 바깥을 향하는 쪽
                tx, ty = ahead.x - point.x, ahead.y - point.y
                norm = math.hypot(tx, ty) or 1.0
                nx, ny = ty / norm, -tx / norm
                probe = Point(point.x + nx * 0.5, point.y + ny * 0.5)
                if polygon.contains(probe):
                    nx, ny = -nx, -ny
                normal_az = math.degrees(math.atan2(nx, ny)) % 360
                # 남향 계열(방위 90~270°)만 채택
                if not 90.0 <= normal_az <= 270.0:
                    continue
                px, py = point.x + nx * 0.4, point.y + ny * 0.4
                for floor in range(1, floors + 1):
                    receptors.append(Receptor(
                        px, py,
                        (floor - 1) * SCHOOL_FLOOR_H + SCHOOL_WINDOW_H,
                        normal_az, label, floor,
                    ))
    return receptors


# --------------------------------------------------------------------------- #
# 일조 계산
# --------------------------------------------------------------------------- #
def sweep_polygon(geom, dx: float, dy: float):
    """폴리곤을 (dx, dy) 방향으로 쓸어낸 영역(선분과의 민코프스키 합).

    각 변이 지나간 평행사변형과 원본·이동본의 합집합으로 정확히 구성한다.
    """
    polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    parts = [geom, translate(geom, dx, dy)]
    for polygon in polygons:
        rings = [polygon.exterior, *polygon.interiors]
        for ring in rings:
            coords = list(ring.coords)
            for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
                band = Polygon([
                    (x0, y0), (x1, y1), (x1 + dx, y1 + dy), (x0 + dx, y0 + dy)
                ])
                if band.is_valid and band.area > 0:
                    parts.append(band)
    return unary_union(parts)


def shadow_polygon(prism: Prism, receptor_z: float, sun_az: float, sun_alt: float):
    """수광 높이 receptor_z 평면에서 prism이 만드는 그림자 영역.

    태양 방향 수평 단위벡터 d에 대해 footprint를 [0, L] 구간만큼 -d 방향으로
    쓸어낸 영역(민코프스키 합)이 정확한 그림자이다. L = (top - z)/tan(alt).
    """
    if prism.top_m <= receptor_z:
        return None
    length = (prism.top_m - receptor_z) / math.tan(math.radians(sun_alt))
    dx = math.sin(math.radians(sun_az)) * length
    dy = math.cos(math.radians(sun_az)) * length
    return sweep_polygon(prism.footprint, -dx, -dy)


def sun_track(month: int, day: int, lat: float, lon: float,
              start: float = 8.0, end: float = 16.0, step_min: int = 10):
    times = []
    steps = int(round((end - start) * 60 / step_min))
    for i in range(steps + 1):
        clock = start + i * step_min / 60.0
        altitude, azimuth = solar_position(lat, lon, month, day, clock)
        if altitude > 0.5:
            times.append((clock, altitude, azimuth))
    return times


def shadow_mask(
    blockers: Sequence[Prism], height: float, azimuth: float, altitude: float, cloud,
):
    """해당 시각·높이에서 차폐물들이 만드는 그림자 합집합(준비된 기하)."""
    shadows = []
    for prism in blockers:
        if prism.top_m <= height:
            continue
        reach = (prism.top_m - height) / math.tan(math.radians(altitude))
        # 그림자가 수광점 구름에 닿을 수 없으면 건너뛴다
        if prism.footprint.distance(cloud) > reach:
            continue
        poly = shadow_polygon(prism, height, azimuth, altitude)
        if poly is not None and not poly.is_empty:
            shadows.append(poly)
    return prep(unary_union(shadows)) if shadows else None


def receptor_cloud(receptors: Sequence[Receptor]):
    return MultiPoint([(r.x, r.y) for r in receptors]).convex_hull.buffer(2.0)


def build_context_masks(
    context: Sequence[Prism],
    receptors: Sequence[Receptor],
    times: Sequence[tuple[float, float, float]],
) -> dict[tuple[int, float], Any]:
    """대안과 무관한 기존 차폐물의 그림자를 미리 계산해 재사용한다."""
    cloud = receptor_cloud(receptors)
    heights = sorted({round(r.z, 2) for r in receptors})
    masks: dict[tuple[int, float], Any] = {}
    for t_index, (_clock, altitude, azimuth) in enumerate(times):
        for height in heights:
            masks[(t_index, height)] = shadow_mask(
                context, height, azimuth, altitude, cloud)
    return masks


def sunlit_flags(
    receptors: Sequence[Receptor],
    towers: Sequence[Prism],
    times: Sequence[tuple[float, float, float]],
    context_masks: dict[tuple[int, float], Any],
) -> list[list[bool]]:
    """수광점 × 시각별 일조 여부 행렬.

    evaluate()가 내부에서 쓰는 계산이며, 시간대별 프로파일이 필요한
    분석(대교 학교 일조영향 등)에서 동일 로직을 재사용하도록 분리했다.
    """
    heights = sorted({round(r.z, 2) for r in receptors})
    by_height: dict[float, list[int]] = {h: [] for h in heights}
    for idx, receptor in enumerate(receptors):
        by_height[round(receptor.z, 2)].append(idx)

    cloud = receptor_cloud(receptors)

    sunlit = [[False] * len(times) for _ in receptors]
    for t_index, (_clock, altitude, azimuth) in enumerate(times):
        for height in heights:
            ctx = context_masks.get((t_index, height))
            own = shadow_mask(towers, height, azimuth, altitude, cloud)
            for idx in by_height[height]:
                receptor = receptors[idx]
                # 창면 자체 향에 따른 자기그늘(태양이 벽 뒤쪽이면 일조 없음).
                # 운동장 지반(수평면)은 방위 제약을 받지 않는다.
                if not receptor.horizontal:
                    delta = abs((azimuth - receptor.normal_az + 180) % 360 - 180)
                    if delta >= 88.0:
                        continue
                point = Point(receptor.x, receptor.y)
                if ctx is not None and ctx.contains(point):
                    continue
                if own is not None and own.contains(point):
                    continue
                sunlit[idx][t_index] = True
    return sunlit


def evaluate(
    receptors: Sequence[Receptor],
    towers: Sequence[Prism],
    times: Sequence[tuple[float, float, float]],
    context_masks: dict[tuple[int, float], Any],
    step_min: int = 10,
) -> list[dict[str, Any]]:
    """수광점별 동지일 일조시간을 계산한다."""
    sunlit = sunlit_flags(receptors, towers, times, context_masks)
    hour_step = step_min / 60.0

    def longest_run(flags: Sequence[bool], lo: float, hi: float) -> float:
        best = run = 0
        for t_index, (clock, _a, _z) in enumerate(times):
            if lo <= clock <= hi and flags[t_index]:
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best * hour_step

    results: list[dict[str, Any]] = []
    for idx, receptor in enumerate(receptors):
        flags = sunlit[idx]
        results.append({
            "building": receptor.building,
            "floor": receptor.floor,
            "x": round(receptor.x, 2),
            "y": round(receptor.y, 2),
            "z": round(receptor.z, 2),
            "normal_az": round(receptor.normal_az, 1),
            "total_h_08_16": round(sum(flags) * hour_step, 2),
            "cont_h_08_16": round(longest_run(flags, 8.0, 16.0), 2),
            "cont_h_09_15": round(longest_run(flags, 9.0, 15.0), 2),
        })
    return results


def summarize(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    if not total:
        return {"n": 0}
    # 기준 A(교육환경평가 일반): 동지일 08~16시 연속 2시간 이상 또는 총 4시간 이상
    pass_a = sum(
        1 for r in results
        if r["cont_h_08_16"] >= 2.0 or r["total_h_08_16"] >= 4.0
    )
    # 기준 B(강화, 고등학교): 동지일 09~15시 연속 2시간 이상
    pass_b = sum(1 for r in results if r["cont_h_09_15"] >= 2.0)
    fails = [r for r in results
             if r["cont_h_08_16"] < 2.0 and r["total_h_08_16"] < 4.0]
    return {
        "n": total,
        "pass_pct": pass_a / total * 100.0,
        "pass_strict_pct": pass_b / total * 100.0,
        "pass_cont_pct": sum(1 for r in results if r["cont_h_08_16"] >= 2.0) / total * 100.0,
        "pass_total_pct": sum(1 for r in results if r["total_h_08_16"] >= 4.0) / total * 100.0,
        "mean_cont_h": sum(r["cont_h_08_16"] for r in results) / total,
        "mean_total_h": sum(r["total_h_08_16"] for r in results) / total,
        "min_cont_h": min(r["cont_h_08_16"] for r in results),
        "n_fail": len(fails),
        # 불충족 수광점이 실제로 확보하는 일조시간(불충족 시 최대확보 기준 비교용)
        "fail_mean_total_h": (sum(r["total_h_08_16"] for r in fails) / len(fails)) if fails else 0.0,
        "fail_mean_cont_h": (sum(r["cont_h_08_16"] for r in fails) / len(fails)) if fails else 0.0,
        "worst_pct": sum(1 for r in results if r["cont_h_08_16"] < 1.0) / total * 100.0,
    }


# --------------------------------------------------------------------------- #
# 배치안 생성
# --------------------------------------------------------------------------- #
def place_tower(
    site: Polygon, long_az: float, u_offset: float, v_offset: float,
    width_u: float, width_v: float, top_m: float, label: str,
    rotate_deg: float = 0.0,
) -> Prism:
    """대지 좌표계(장변 u, 단변 v) 기준으로 타워를 배치한다.

    ``rotate_deg`` 는 대지 격자 대비 추가 회전각(반시계, 도)이다.
    방위각 az 는 수학각 (90 - az) 에 대응하므로 장변 정렬 회전은 90 - long_az.
    """
    centre = site.centroid
    ang = math.radians(long_az)
    ux, uy = math.sin(ang), math.cos(ang)
    vx, vy = math.sin(ang + math.pi / 2), math.cos(ang + math.pi / 2)
    cx = centre.x + ux * u_offset + vx * v_offset
    cy = centre.y + uy * u_offset + vy * v_offset
    base = Polygon([
        (cx - width_u / 2, cy - width_v / 2), (cx + width_u / 2, cy - width_v / 2),
        (cx + width_u / 2, cy + width_v / 2), (cx - width_u / 2, cy + width_v / 2),
    ])
    return Prism(
        rotate(base, 90.0 - long_az + rotate_deg, origin=(cx, cy)),
        top_m, label,
    )


def compose_scheme(
    code: str, name: str, site: Polygon, long_az: float, long_len: float,
    gfa_target: float, max_footprint: float, away: float,
    count: int, unit_area: float, v_ratio: float,
    slab: bool = False, rotate_deg: float = 0.0, u_shift: float = 0.0,
) -> Scheme:
    """파라미터로부터 배치안 하나를 만든다."""
    short_len = site.area / long_len
    if count * unit_area > max_footprint:
        unit_area = max_footprint / count
    floors = int(gfa_target / (count * unit_area))
    if slab:
        width_u, width_v = 78.0, unit_area / 78.0
    else:
        width_v = math.sqrt(unit_area / 0.886)   # 정오 그림자 폭 최소화 비율
        width_u = unit_area / width_v
    diag = math.hypot(width_u, width_v) if rotate_deg else max(width_u, width_v)
    v_offset = away * v_ratio * max(0.0, short_len / 2 - diag / 2 - 6.0)
    towers = []
    for i in range(count):
        span = max(0.0, long_len - diag - 24.0)
        u_offset = u_shift + (0.0 if count == 1
                              else -span / 2 + span * i / (count - 1))
        towers.append(place_tower(
            site, long_az, u_offset, v_offset, width_u, width_v,
            floors * RESI_FLOOR_H + ROOFTOP_M, f"{code}-{i + 1}", rotate_deg,
        ))
    return Scheme(code, name, towers,
                  f"{count}개동 · 동당 {unit_area:,.0f}㎡ · {floors}층")


def make_presets(
    site: Polygon, long_az: float, long_len: float,
    gfa_target: float, max_footprint: float, school_side_sign: float,
) -> list[Scheme]:
    """유형 비교용 대표 배치안."""
    away = -school_side_sign
    specs = [
        ("A1", "탑상형 1개동 (700㎡)", 1, 700.0, 0.55, False),
        ("A2", "탑상형 1개동 (620㎡)", 1, 620.0, 0.55, False),
        ("A3", "탑상형 1개동 (540㎡ 초슬림)", 1, 540.0, 0.55, False),
        ("B1", "탑상형 2개동 (학교 반대측)", 2, 560.0, 0.55, False),
        ("B2", "탑상형 2개동 (대지 중앙)", 2, 560.0, 0.0, False),
        ("B3", "탑상형 2개동 (학교측)", 2, 560.0, -0.55, False),
        ("C1", "탑상형 3개동", 3, 470.0, 0.55, False),
        ("D1", "판상형 2개동 (비교군)", 2, 1100.0, 0.55, True),
    ]
    return [
        compose_scheme(code, name, site, long_az, long_len, gfa_target,
                       max_footprint, away, count, area, v_ratio, slab)
        for code, name, count, area, v_ratio, slab in specs
    ]


def sweep_candidates(
    site: Polygon, long_az: float, long_len: float,
    gfa_target: float, max_footprint: float, school_side_sign: float,
) -> list[Scheme]:
    """탐색용 후보군 – 동수·기준층면적·배치·회전각 격자 탐색."""
    away = -school_side_sign
    candidates: list[Scheme] = []
    index = 0
    grid = [
        (1, (420.0, 480.0, 540.0, 600.0, 680.0), (1.0, 0.7, 0.4, 0.0, -0.5),
         (0.0, 30.0), (-25.0, 0.0, 25.0)),
        (2, (440.0, 520.0), (1.0, 0.4), (0.0, 30.0), (0.0,)),
    ]
    for count, areas, v_ratios, rotations, shifts in grid:
        for unit_area in areas:
            for v_ratio in v_ratios:
                for rotate_deg in rotations:
                    for u_shift in shifts:
                        index += 1
                        candidates.append(compose_scheme(
                            f"S{index:03d}",
                            f"{count}동·{unit_area:.0f}㎡·v{v_ratio:+.1f}"
                            f"·r{rotate_deg:.0f}°·u{u_shift:+.0f}",
                            site, long_az, long_len, gfa_target, max_footprint,
                            away, count, unit_area, v_ratio,
                            rotate_deg=rotate_deg, u_shift=u_shift,
                        ))
    return candidates


def make_floor_band(
    site: Polygon, long_az: float, long_len: float,
    gfa_target: float, max_footprint: float, school_side_sign: float,
    floor_list: Sequence[int] = (35, 40, 44, 46, 49, 52, 55, 60, 70, 80),
) -> list[Scheme]:
    """층수를 고정하고 기준층 면적을 역산한 층수대별 대안(트레이드오프 곡선용)."""
    away = -school_side_sign
    schemes: list[Scheme] = []
    for count in (1, 2):
        for floors in floor_list:
            if count == 2 and floors > 60:
                continue
            unit_area = gfa_target / (count * floors)
            if unit_area < 380.0:          # 코어 포함 최소 기준층 한계
                continue
            schemes.append(compose_scheme(
                f"F{count}-{floors}", f"{count}개동 {floors}층",
                site, long_az, long_len, gfa_target, max_footprint,
                away, count, unit_area, 1.0,
            ))
    return schemes


def per_school(results: Sequence[dict[str, Any]]) -> dict[str, float]:
    """학교(지번)별 일조 충족률."""
    schools = sorted({r["building"] for r in results})
    return {
        j: summarize([r for r in results if r["building"] == j])["pass_pct"]
        for j in schools
    }


def worst_degradation(base: dict[str, float], scheme: dict[str, float]) -> float:
    """기준선 대비 학교별 충족률 저하폭 중 최악값(음수일수록 나쁨)."""
    return min(scheme[j] - base.get(j, 0.0) for j in scheme) if scheme else 0.0


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="화랑아파트 재건축 학교 일조영향 배치 검토")
    parser.add_argument("--buildings", type=Path, required=True, help="AL_D010 GeoPackage")
    parser.add_argument("--daegyo", type=Path,
                        default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"),
                        help="대교아파트 신축 폴리곤 (GeoJSON 또는 GPKG)")
    parser.add_argument("--outdir", type=Path, default=Path("outputs/hwarang"))
    parser.add_argument("--site-area", type=float, default=9395.0, help="화랑 대지면적(㎡)")
    parser.add_argument("--far", type=float, default=400.0, help="허용 용적률(%%)")
    parser.add_argument("--bcr", type=float, default=60.0, help="허용 건폐율(%%)")
    parser.add_argument("--date", type=str, default="12-22", help="분석 기준일 MM-DD")
    parser.add_argument("--school-radius", type=float, default=350.0,
                        help="검토 대상 학교 반경(m)")
    parser.add_argument("--step-min", type=int, default=10, help="시간 간격(분)")
    parser.add_argument("--no-sweep", action="store_true", help="격자탐색 생략")
    parser.add_argument("--top-n", type=int, default=3, help="격자탐색 상위 N개 정밀평가")
    parser.add_argument("--max-floors", type=int, default=60,
                        help="권장안 선정 시 현실적 최고층수 상한(초과 대안도 평가는 수행)")
    return parser.parse_args(argv)


def prepare_analysis(args) -> dict[str, Any]:
    """대상지·수광점·차폐물·태양궤적을 준비한다(형상 검토 스크립트와 공용)."""
    from pyproj import CRS, Transformer

    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_buildings(args.buildings)
    site = build_site(buildings, args.site_area)
    long_az, long_len = site_axes(site)
    centre = site.centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)

    schools = [
        b for b in buildings
        if b["use"] == SCHOOL_USE and b["floors"] > 0
        and b["geom"].centroid.distance(centre) <= args.school_radius
    ]
    receptors = make_receptors(schools)

    # 학교가 대지 단변(v축) 기준 어느 쪽에 있는지
    ang = math.radians(long_az)
    vx, vy = math.sin(ang + math.pi / 2), math.cos(ang + math.pi / 2)
    school_v = sum(
        ((b["geom"].centroid.x - centre.x) * vx + (b["geom"].centroid.y - centre.y) * vy)
        for b in schools
    )
    school_side = 1.0 if school_v > 0 else -1.0

    # 화랑 부지 외 기존 건물 + 대교 신축을 공통 차폐물로
    context: list[Prism] = []
    for row in buildings:
        if row["jibun"] == HWARANG_JIBUN or row["floors"] <= 0:
            continue
        if row["geom"].centroid.distance(centre) > 400:
            continue
        geom = row["geom"]
        polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for polygon in polygons:
            context.append(Prism(polygon, building_height(row),
                                 f"기존 {row['jibun']} {row['name']}".strip()))
    context += load_daegyo(args.daegyo)

    existing_hwarang = [
        Prism(b["geom"], b["floors"] * USE_FLOOR_HEIGHT["공동주택"] + 2.0,
              f"화랑 기존 {b['name']}")
        for b in buildings if b["jibun"] == HWARANG_JIBUN
    ]
    times = sun_track(month, day, lat, lon, step_min=args.step_min)
    school_az = (math.degrees(math.atan2(vx, vy)) + (0 if school_side > 0 else 180)) % 360
    return {
        "buildings": buildings, "site": site, "long_az": long_az, "long_len": long_len,
        "centre": centre, "lat": lat, "lon": lon, "schools": schools,
        "receptors": receptors, "school_side": school_side, "school_az": school_az,
        "context": context, "existing_hwarang": existing_hwarang, "times": times,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    ctx = prepare_analysis(args)
    site, long_az, long_len = ctx["site"], ctx["long_az"], ctx["long_len"]
    schools, receptors = ctx["schools"], ctx["receptors"]
    school_side, school_az = ctx["school_side"], ctx["school_az"]
    context, existing_hwarang, times = ctx["context"], ctx["existing_hwarang"], ctx["times"]

    gfa_target = args.site_area * args.far / 100.0
    max_footprint = args.site_area * args.bcr / 100.0

    print(f"대상지: 화랑아파트 여의도동 40-4  대지 {args.site_area:,.0f}㎡ "
          f"({long_len:.0f} × {site.area / long_len:.0f} m, 장변 방위 {long_az:.1f}°)")
    print(f"허용: 용적률 {args.far:.0f}% → 지상연면적 {gfa_target:,.0f}㎡ / "
          f"건폐율 {args.bcr:.0f}% → 건축면적 {max_footprint:,.0f}㎡")
    print(f"학교: {len(schools)}개 동, 수광점 {len(receptors):,}점 "
          f"(대지 기준 {compass_name(school_az)}측 {school_az:.0f}°)")
    print(f"차폐물: 기존 {len(context)}개 + 대안별 타워, 시각 {len(times)}스텝\n")

    schemes = make_presets(site, long_az, long_len, gfa_target, max_footprint, school_side)
    schemes += make_floor_band(site, long_az, long_len, gfa_target,
                               max_footprint, school_side)

    if not args.no_sweep:
        coarse = [r for r in receptors if r.floor <= 2][::2]
        coarse_times = times[::2]
        coarse_masks = build_context_masks(context, coarse, coarse_times)
        candidates = sweep_candidates(site, long_az, long_len, gfa_target,
                                      max_footprint, school_side)
        print(f"1단계 격자탐색: 후보 {len(candidates)}개 "
              f"(저층부 수광점 {len(coarse)}점 · {len(coarse_times)}스텝)")
        base_school = per_school(evaluate(coarse, [], coarse_times,
                                          coarse_masks, args.step_min * 2))
        scored = []
        for scheme in candidates:
            res = evaluate(coarse, scheme.towers, coarse_times,
                           coarse_masks, args.step_min * 2)
            schools_pct = per_school(res)
            scored.append((worst_degradation(base_school, schools_pct),
                           summarize(res)["pass_pct"], scheme.max_floors, scheme))
        # ① 학교별 최악 저하폭 최소화 ② 전체 충족률 ③ 층수
        scored.sort(key=lambda s: (-round(s[0], 1), -round(s[1], 1), -s[2]))
        for rank, (worst, pass_pct, floors, scheme) in enumerate(scored[:args.top_n], 1):
            scheme.code = f"OPT{rank}"
            scheme.name = f"최적탐색{rank} · {scheme.name}"
            schemes.append(scheme)
            print(f"   {rank}위 {scheme.name[:44]:<44} 최악저하 {worst:+5.1f}%p "
                  f"충족 {pass_pct:5.1f}% {floors}F")
        print()

    baselines = [
        Scheme("Z0", "화랑 공지(영향 없음 기준선)", [], "화랑 철거 후 나지 상태"),
        Scheme("Z1", "화랑 현황 10층 3개동", list(existing_hwarang), "현재 상태"),
    ]

    masks = build_context_masks(context, receptors, times)
    rows: list[dict[str, Any]] = []
    detail: dict[str, list[dict[str, Any]]] = {}
    print("2단계 정밀평가 (전 층 수광점)")
    for scheme in baselines + schemes:
        results = evaluate(receptors, scheme.towers, times, masks, args.step_min)
        stats = summarize(results)
        detail[scheme.code] = results
        rows.append({"scheme": scheme, "stats": stats})
        print(f"[{scheme.code:<5}] {scheme.name[:34]:<34} "
              f"기준A {stats['pass_pct']:5.1f}% | 기준B {stats['pass_strict_pct']:5.1f}% | "
              f"불충족 {stats['n_fail']:3d}점(평균 {stats['fail_mean_total_h']:.2f}h) | "
              f"최고 {scheme.max_floors:2d}F | 용적률 {scheme.gfa_m2 / args.site_area * 100:5.1f}%")

    print("\n학교(지번)별 일조 충족률")
    codes = [r["scheme"].code for r in rows]
    jibuns = sorted({r["building"] for r in detail[codes[0]]})
    print("  " + "지번".ljust(8) + "".join(c.rjust(8) for c in codes))
    for jibun in jibuns:
        cells = []
        for code in codes:
            subset = [r for r in detail[code] if r["building"] == jibun]
            cells.append(f"{summarize(subset)['pass_pct']:7.1f}%")
        print("  " + jibun.ljust(8) + "".join(cells))

    write_results(args, site, rows, detail, receptors, long_az,
                  context, times, schools)
    return 0




def export_preview_svg(args, site, scheme, schools, context_prisms, sun_times) -> None:
    """대지·타워·학교·시각별 그림자를 한 장의 SVG 도면으로 출력한다."""
    if not scheme.towers:
        return
    shadow_sets = []
    for target, colour in ((9.0, "#f4a261"), (12.5, "#e76f51"), (15.0, "#8ab17d")):
        clock, altitude, azimuth = min(sun_times, key=lambda t: abs(t[0] - target))
        polys = [shadow_polygon(t, SCHOOL_WINDOW_H, azimuth, altitude)
                 for t in scheme.towers]
        polys = [p for p in polys if p is not None and not p.is_empty]
        if polys:
            shadow_sets.append((unary_union(polys), colour,
                                f"{int(clock):02d}:{int(round((clock % 1) * 60)):02d}"))

    layers = [site, *(t.footprint for t in scheme.towers),
              *(s["geom"] for s in schools), *(g for g, _c, _l in shadow_sets)]
    bounds = unary_union(layers).bounds
    pad = 20.0
    minx, miny, maxx, maxy = bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad
    width, height = maxx - minx, maxy - miny
    px_w = 1000.0
    scale_px = px_w / width

    def path(geom) -> str:
        polygons = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        parts = []
        for polygon in polygons:
            pts = " ".join(
                f"{(x - minx) * scale_px:.1f},{(maxy - y) * scale_px:.1f}"
                for x, y in polygon.exterior.coords)
            parts.append(f'<polygon points="{pts}" />')
        return "".join(parts)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{height * scale_px:.0f}" viewBox="0 0 {px_w:.0f} {height * scale_px:.0f}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>']
    for geom, colour, label in shadow_sets:
        out.append(f'<g fill="{colour}" fill-opacity="0.33" stroke="none">{path(geom)}</g>')
    for school in schools:
        out.append(f'<g fill="#2a6f97" fill-opacity="0.75" stroke="#14425c" '
                   f'stroke-width="1">{path(school["geom"])}</g>')
    for prism in context_prisms:
        out.append(f'<g fill="#c9c9c4" fill-opacity="0.45" stroke="none">'
                   f'{path(prism.footprint)}</g>')
    out.append(f'<g fill="none" stroke="#e63946" stroke-width="2.5" '
               f'stroke-dasharray="9 5">{path(site)}</g>')
    for tower in scheme.towers:
        out.append(f'<g fill="#1d3557" fill-opacity="0.9" stroke="#0b1c30" '
                   f'stroke-width="2">{path(tower.footprint)}</g>')
    y = 24.0
    out.append(f'<text x="14" y="{y}" font-family="sans-serif" font-size="17" '
               f'font-weight="bold" fill="#111">화랑아파트 재건축 {scheme.name} '
               f'– 동지일 그림자 (1층 창 높이)</text>')
    for _g, colour, label in shadow_sets:
        y += 21
        out.append(f'<rect x="14" y="{y - 11:.0f}" width="14" height="12" fill="{colour}" '
                   f'fill-opacity="0.55"/>'
                   f'<text x="34" y="{y:.0f}" font-family="sans-serif" font-size="13" '
                   f'fill="#333">{label} 그림자</text>')
    y += 21
    out.append(f'<rect x="14" y="{y - 11:.0f}" width="14" height="12" fill="#2a6f97"/>'
               f'<text x="34" y="{y:.0f}" font-family="sans-serif" font-size="13" '
               f'fill="#333">주변 학교</text>')
    out.append('</svg>')
    (args.outdir / f"hwarang_preview_{scheme.code}.svg").write_text(
        "\n".join(out), encoding="utf-8")


def export_shadows(args, scheme, context_prisms, sun_times) -> None:
    """권장안의 시각별 그림자 폴리곤을 1층 창 높이 기준으로 출력한다."""
    if not scheme.towers or not sun_times:
        return
    targets = [8.0, 9.0, 10.0, 12.5, 14.0, 15.0, 16.0]
    features = []
    for target in targets:
        clock, altitude, azimuth = min(sun_times, key=lambda t: abs(t[0] - target))
        for prisms, kind in ((scheme.towers, "화랑 신축"), (context_prisms, "기존·대교")):
            polys = []
            for prism in prisms:
                poly = shadow_polygon(prism, SCHOOL_WINDOW_H, azimuth, altitude)
                if poly is not None and not poly.is_empty:
                    polys.append(poly)
            if not polys:
                continue
            merged = unary_union(polys)
            features.append((merged, {
                "kind": kind,
                "time_kst": f"{int(clock):02d}:{int(round((clock % 1) * 60)):02d}",
                "sun_alt_deg": round(altitude, 2),
                "sun_az_deg": round(azimuth, 2),
                "scheme": scheme.code,
            }))
    payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
        "features": [
            {"type": "Feature", "geometry": mapping(g), "properties": p}
            for g, p in features
        ],
    }
    path = args.outdir / f"hwarang_shadows_{scheme.code}.geojson"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_results(args, site, rows, detail, receptors, long_az,
                  context_prisms=(), sun_times=(), schools_meta=()) -> None:
    """배치안·수광점 결과와 비교표를 파일로 출력한다."""
    def dump_geojson(path: Path, features: list[tuple[Any, dict[str, Any]]]) -> None:
        payload = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
            "features": [
                {"type": "Feature", "geometry": mapping(g), "properties": p}
                for g, p in features
            ],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    dump_geojson(args.outdir / "hwarang_site.geojson",
                 [(site, {"name": "화랑아파트 재건축 대지(추정)",
                          "area_m2": round(site.area, 1)})])

    for row in rows:
        scheme = row["scheme"]
        if not scheme.towers:
            continue
        features = [
            (t.footprint, {
                "scheme": scheme.code, "id": t.label,
                "floors": Scheme.floors_of(t),
                "height_m": round(t.top_m, 2),
                "base_elev_m": GROUND_ELEV_M,
                "top_elev_m": round(GROUND_ELEV_M + t.top_m, 2),
                "area_m2": round(t.footprint.area, 1),
            })
            for t in scheme.towers
        ]
        dump_geojson(args.outdir / f"hwarang_scheme_{scheme.code}.geojson", features)

    base_school = per_school(detail["Z0"])
    eligible = [
        r for r in rows
        if r["scheme"].code not in {"Z0", "Z1"}
        and r["scheme"].max_floors <= args.max_floors
        and r["scheme"].gfa_m2 / args.site_area * 100 >= args.far - 10
    ]
    best = max(
        eligible or rows[2:],
        key=lambda r: (round(worst_degradation(base_school,
                                               per_school(detail[r["scheme"].code])), 1),
                       round(r["stats"]["pass_pct"], 1), r["scheme"].max_floors),
    )
    export_shadows(args, best["scheme"], context_prisms, sun_times)
    export_preview_svg(args, site, best["scheme"], schools_meta,
                       context_prisms, sun_times)
    results = detail[best["scheme"].code]
    with (args.outdir / "receptor_results_best.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    with (args.outdir / "scheme_comparison.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["코드", "배치안", "설명", "동수", "최고층수", "건축면적㎡",
                         "지상연면적㎡", "용적률%", "건폐율%", "세대평균높이m",
                         "기준A충족%", "기준B충족%", "연속2h충족%", "총4h충족%",
                         "평균연속h", "평균총h", "불충족점수", "불충족평균확보h"])
        for row in rows:
            s, st = row["scheme"], row["stats"]
            writer.writerow([
                s.code, s.name, s.description, len(s.towers), s.max_floors,
                round(s.footprint_m2, 1), round(s.gfa_m2, 1),
                round(s.gfa_m2 / args.site_area * 100, 1),
                round(s.footprint_m2 / args.site_area * 100, 1),
                round(s.mean_home_elev_m, 1),
                round(st["pass_pct"], 1), round(st["pass_strict_pct"], 1),
                round(st["pass_cont_pct"], 1), round(st["pass_total_pct"], 1),
                round(st["mean_cont_h"], 2), round(st["mean_total_h"], 2),
                st["n_fail"], round(st["fail_mean_total_h"], 2),
            ])
    print(f"\n권장안: [{best['scheme'].code}] {best['scheme'].name}")
    print(f"결과 저장: {args.outdir}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
