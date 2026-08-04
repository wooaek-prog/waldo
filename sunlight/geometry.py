"""개략 3D 매싱(수직 압출된 다각형, prism)에 대한 레이-차폐 판정.

건물은 '평면 다각형(footprint) + 바닥표고(base_z) + 높이(height)'로 압출된
수직 프리즘으로 단순화한다. 정밀 BIM/실측 모델이 아니어도 '개략 매싱' 수준의
일영(그림자) 판정에는 충분한 근사이며, 옥탑/발코니 등 세부 형상은 반영하지 않는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

Point2 = tuple[float, float]


def point_in_polygon(point: Point2, polygon: Sequence[Point2]) -> bool:
    """표준 레이캐스팅 알고리즘. 단순(비자기교차) 다각형에 대해 동작한다."""
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _ray_segment_t(
    origin: Point2, direction: Point2, p1: Point2, p2: Point2
) -> float | None:
    """레이 origin+t*direction (t: 임의 실수)와 선분 p1-p2의 교차 매개변수 t.

    선분 위의 점이 아니면 None. (표준 2D 레이-선분 교차의 cross-product 공식)
    """
    rx, ry = direction
    sx, sy = p2[0] - p1[0], p2[1] - p1[1]
    denom = rx * sy - ry * sx
    if abs(denom) < 1e-12:
        return None
    dx, dy = p1[0] - origin[0], p1[1] - origin[1]
    t = (dx * sy - dy * sx) / denom
    u = (dx * ry - dy * rx) / denom
    if 0.0 <= u <= 1.0:
        return t
    return None


def polygon_ray_xy_intervals(
    polygon: Sequence[Point2],
    origin: Point2,
    direction: Point2,
    t_max: float,
) -> list[tuple[float, float]]:
    """레이(origin + t*direction, 0<=t<=t_max)가 다각형 내부에 있는 t 구간 목록.

    다각형 경계와의 교차점들을 t값 기준으로 정렬한 뒤, 각 구간의 중점이
    다각형 내부인지 point-in-polygon으로 판정하는 방식(선분-다각형 클리핑).
    오목 다각형도 정확히 처리한다.
    """
    dx, dy = direction
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        return []

    ts = {0.0, t_max}
    n = len(polygon)
    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]
        t = _ray_segment_t(origin, direction, p1, p2)
        if t is not None and -1e-9 <= t <= t_max:
            ts.add(t)

    sorted_ts = sorted(ts)
    intervals: list[tuple[float, float]] = []
    for a, b in zip(sorted_ts, sorted_ts[1:]):
        if b - a < 1e-9:
            continue
        mid = (a + b) / 2.0
        point = (origin[0] + mid * dx, origin[1] + mid * dy)
        if point_in_polygon(point, polygon):
            intervals.append((a, b))
    return _merge_intervals(intervals)


def _merge_intervals(
    intervals: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    if not intervals:
        return []
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for a, b in intervals[1:]:
        last_a, last_b = merged[-1]
        if a <= last_b + 1e-9:
            merged[-1] = (last_a, max(last_b, b))
        else:
            merged.append((a, b))
    return merged


def _intersect_interval_with_range(
    intervals: list[tuple[float, float]], lo: float, hi: float
) -> list[tuple[float, float]]:
    result = []
    for a, b in intervals:
        na, nb = max(a, lo), min(b, hi)
        if nb - na > 1e-9:
            result.append((na, nb))
    return result


@dataclass(frozen=True)
class BuildingMass:
    """수직 압출된 다각형(prism) 형태의 개략 건물 매싱."""

    id: str
    name: str
    footprint: tuple[Point2, ...]
    base_z: float
    height: float

    @property
    def top_z(self) -> float:
        return self.base_z + self.height

    def ray_blocked(
        self,
        origin: tuple[float, float, float],
        direction: tuple[float, float, float],
        t_max: float = 2000.0,
        epsilon: float = 1e-3,
    ) -> bool:
        """origin에서 direction(정규화된 태양 방향) 쪽으로 쏜 레이가 이 매싱에
        t>epsilon 구간에서 가로막히는지 여부."""
        ox, oy, oz = origin
        dx, dy, dz = direction

        xy_intervals = polygon_ray_xy_intervals(
            self.footprint, (ox, oy), (dx, dy), t_max
        )
        if not xy_intervals:
            return False

        if abs(dz) < 1e-12:
            if not (self.base_z <= oz <= self.top_z):
                return False
            z_lo, z_hi = 0.0, t_max
        else:
            t_base = (self.base_z - oz) / dz
            t_top = (self.top_z - oz) / dz
            z_lo, z_hi = (t_base, t_top) if t_base <= t_top else (t_top, t_base)

        blocked = _intersect_interval_with_range(xy_intervals, max(z_lo, 0.0), min(z_hi, t_max))
        blocked = _intersect_interval_with_range(blocked, epsilon, t_max)
        return len(blocked) > 0


def any_blocked(
    masses: Sequence[BuildingMass],
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    exclude_ids: Sequence[str] = (),
    t_max: float = 2000.0,
) -> bool:
    """masses 중 exclude_ids를 제외한 임의의 매싱이 시야를 가리면 True."""
    for mass in masses:
        if mass.id in exclude_ids:
            continue
        if mass.ray_blocked(origin, direction, t_max=t_max):
            return True
    return False


def unit(vec: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(c * c for c in vec))
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (vec[0] / length, vec[1] / length, vec[2] / length)
