#!/usr/bin/env python3
"""화랑아파트 재건축 – '신규 불충족 최소' 설계안 다안 비교.

우선순위(사용자 지정, 2026-09-18)
    1. 교육환경평가 기준A 가 **신규로 불충족되지 않게**. 불가하면 최저로.
    2. 최소 35층 ~ 최대 60층, 용적률 400%
    3. 면적은 **서비스면적(발코니) 포함**해 함께 낸다
    4. 저층부·2타워 등 **여러 안**을 검토한다

검토하는 안의 갈래는 다섯이다.

    단일         타워 1개동
    단일+저층부    타워 1개동 + 저층부(타워가 저층부 위에 얹힌다)
    타워+저층별동   타워 1개동 + 저층 판상형 **별동**(서로 떨어져 있다)
    쌍둥이       타워 2개동(높이·크기 각각 다를 수 있음)
    쌍둥이+저층부   타워 2개동 + 저층부

용적률이 400%로 고정이라 **연면적 = Σ(기준층 × 층수)가 상수**다. 그래서
설계는 같은 연면적을 '어디에·얼마나 높게' 담느냐의 문제가 된다. 물리적으로
쓸 수 있는 지렛대는 셋이다.

    층수를 올린다      기준층이 작아져 그림자 폭이 좁아진다(대신 길어진다)
    저층부로 옮긴다    저층부는 낮아 그림자가 짧다(대신 넓다)
    두 동으로 나눈다   각 동이 좁아진다. 남북으로 겹쳐 세우면 그림자도 겹친다
    별동으로 떼어낸다  저층부를 학교 반대쪽에 몰아 둘 수 있다

어느 지렛대가 유리한지는 학교 배치에 달려 있어 단정할 수 없으므로 전부 돌린다.

대지·학교 배치 (실측)
    대지        122.7m(방위 52°) × 76.5m, 9,395㎡, 중심 E194,302.9/N546,960.4
    여의도여고   최단 **7.1m**, 중심방위 334.9°   ← 붙어 있다. 가장 민감
    여의도초    최단 17.6m, 중심방위 50.9°
    여의도중    최단 27.5m, 중심방위 7.7°
    여의도고    최단 230.1m, 중심방위 338.4°
    동지 그림자는 북서(300°)→북(0°)→북동(60°)로 쓸려 네 학교를 모두 훑는다.

    python3 hwarang_design_2026.py --buildings <AL_D010.gpkg>
    python3 hwarang_design_2026.py --buildings <...> --budget 2400
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import (
    HWARANG_JIBUN, apartment_prisms, load_named_buildings, school_parcel_rows,
)
from hwarang_redesign import BALCONY_M, COVERAGE_INSET_M, plate_polygon
from school_receptor_compliance import build_context, build_points, pass_a

RESI_FLOOR_H = H.RESI_FLOOR_H       # 2.95
ROOFTOP_M = H.ROOFTOP_M             # 4.0
# 기준층(연면적선)의 물리적 하한. 모듈 전역변수로 두고 --min-plate/--min-short
# 로 바꾼다 — 이 값이 탐색공간을 통째로 가르기 때문이다.
#
# 단변은 평면 형식을 정한다.
#     8.5m   세대깊이 7.2 + 복도 1.3. 편복도형 극단. 세대가 작아진다(전용 50㎡대)
#    11.0m   편복도형 표준
#    14.0m   양면복도형(세대깊이 7.2 x 2). 평면이 가장 편하다
# 좁을수록 그림자 폭이 좁아 학교에 유리하고, 넓을수록 평면이 편하다. 어느 쪽을
# 고를지는 설계 판단이므로 **세 문턱의 최우수안을 모두 낸다**(DEPTH_TIERS).
MIN_TOWER_PLATE = 280.0
MIN_TOWER_SHORT = 8.5
DEPTH_TIERS = ((8.5, "편복도형 극단"), (11.0, "편복도형 표준"),
               (14.0, "양면복도형"))
FAMILIES = ("단일", "단일+저층부", "타워+저층별동", "쌍둥이", "쌍둥이+저층부",
            "2개동",
            "3개동", "4개동", "5개동", "6개동")


# --------------------------------------------------------------------------- #
# 설계안
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Tower:
    plate: float        # 기준층 연면적선(㎡)
    aspect: float       # 장변:단변
    azimuth: float      # 장변 방위
    cx: float
    cy: float
    floors: int         # 최고 층수(지상 기준)

    def dims(self) -> tuple[float, float]:
        short = math.sqrt(self.plate / self.aspect)
        return self.plate / short, short

    def top_m(self) -> float:
        return self.floors * RESI_FLOOR_H + ROOFTOP_M

    def plate_poly(self) -> Polygon:
        """연면적선(발코니 제외) — 용적률 산정용."""
        return plate_polygon(self.plate, self.aspect, self.azimuth,
                             self.cx, self.cy)

    def outline(self) -> Polygon:
        return plate_polygon(self.plate, self.aspect, self.azimuth,
                             self.cx, self.cy, BALCONY_M)

    def coverage(self) -> Polygon:
        return plate_polygon(self.plate, self.aspect, self.azimuth,
                             self.cx, self.cy, BALCONY_M - COVERAGE_INSET_M)


@dataclass(frozen=True)
class Podium:
    plate: float
    aspect: float
    azimuth: float
    cx: float
    cy: float
    floors: int

    def dims(self) -> tuple[float, float]:
        short = math.sqrt(self.plate / self.aspect)
        return self.plate / short, short

    def top_m(self) -> float:
        return self.floors * RESI_FLOOR_H

    def plate_poly(self) -> Polygon:
        """연면적선(발코니 제외) — 용적률 산정용."""
        return plate_polygon(self.plate, self.aspect, self.azimuth,
                             self.cx, self.cy)

    def outline(self) -> Polygon:
        return plate_polygon(self.plate, self.aspect, self.azimuth,
                             self.cx, self.cy, BALCONY_M)

    def coverage(self) -> Polygon:
        return plate_polygon(self.plate, self.aspect, self.azimuth,
                             self.cx, self.cy, BALCONY_M - COVERAGE_INSET_M)


@dataclass(frozen=True)
class Design:
    family: str
    towers: tuple[Tower, ...]
    podium: Podium | None = None
    detached: bool = False
    """저층부가 타워와 **떨어진 별동**인가.

    False — 타워가 저층부 위에 얹힌다(포디움). 타워의 1~저층부층은 저층부
            면적으로 세므로 타워는 그 위 층만 제 면적으로 센다.
    True  — 저층 판상형 별동 + 타워. 서로 겹치지 않고, 각자 1층부터 센다.
            저층 별동을 학교에서 먼 쪽에 따로 둘 수 있어 배치 자유도가 크다.
    """

    # ── 제원 ────────────────────────────────────────────────────────────
    @property
    def pod_floors(self) -> int:
        """저층부 층수. 별동이면 겹치지 않아 연면적에서 빠지는 게 없다."""
        if self.podium is None or self.detached:
            return 0
        return self.podium.floors

    def overlap_m2(self, t: Tower) -> float:
        """타워 기준층이 저층부와 겹치는 면적(연면적선 기준)."""
        if self.podium is None:
            return 0.0
        return t.plate_poly().intersection(self.podium.plate_poly()).area

    @property
    def gfa_m2(self) -> float:
        """연면적. 저층부와 타워가 겹치는 층은 한 번만 센다.

        타워가 저층부 위에 온전히 얹히면 겹침 = 타워 기준층이라 종전 식
        (타워 x (층수-저층부층수) + 저층부 x 저층부층수)과 같아진다. 타워가
        저층부 밖으로 조금 나가면 그 부분은 1~저층부층에서도 타워 몫이므로
        따로 더한다 — 겹침만 빼면 모든 경우가 한 식으로 정리된다.
        """
        p = self.podium
        pf = p.floors if p else 0
        g = p.plate * pf if p else 0.0
        for t in self.towers:
            g += t.plate * (t.floors - pf)
            g += (t.plate - self.overlap_m2(t)) * pf
        return g

    @property
    def service_m2(self) -> float:
        """발코니(서비스면적) 합 — 연면적에서 빠지지만 실면적에는 들어간다."""
        out = 0.0
        for t in self.towers:
            lng, sht = t.dims()
            ring = (lng + 2 * BALCONY_M) * (sht + 2 * BALCONY_M) - t.plate
            out += ring * (t.floors - self.pod_floors)
        if self.podium:
            lng, sht = self.podium.dims()
            ring = (lng + 2 * BALCONY_M) * (sht + 2 * BALCONY_M) - self.podium.plate
            out += ring * self.podium.floors
        return out

    @property
    def real_m2(self) -> float:
        """실면적 = 연면적 + 서비스면적."""
        return self.gfa_m2 + self.service_m2

    @property
    def top_floors(self) -> int:
        return max(t.floors for t in self.towers)

    @property
    def height_m(self) -> float:
        return max(t.top_m() for t in self.towers)

    def coverage_m2(self) -> float:
        return unary_union([m.coverage() for m in self.masses()]).area

    # ── 기하 ────────────────────────────────────────────────────────────
    def masses(self) -> list[Tower | Podium]:
        out: list[Tower | Podium] = list(self.towers)
        if self.podium:
            out.append(self.podium)
        return out

    def prisms(self) -> list[H.Prism]:
        """그림자용 프리즘. 외형선(발코니 끝) 기준.

        타워는 저층부 위에 얹히지만, 프리즘은 지상부터 세운다 — 저층부가
        그 밑을 채우고 있어 실제 형상과 같고, 계산도 단순해진다.
        """
        out = [H.Prism(t.outline(), t.top_m(), f"화랑 타워{i+1} {t.floors}F")
               for i, t in enumerate(self.towers)]
        if self.podium:
            out.append(H.Prism(self.podium.outline(), self.podium.top_m(),
                               f"화랑 저층부 {self.podium.floors}F"))
        return out

    def outlines(self) -> list[tuple[str, Polygon, float]]:
        out = [(f"타워{i+1} 외형선", t.outline(), t.top_m())
               for i, t in enumerate(self.towers)]
        if self.podium:
            out.append(("저층부 외형선", self.podium.outline(),
                        self.podium.top_m()))
        return out

    def label(self) -> str:
        parts = []
        for t in self.towers:
            lng, sht = t.dims()
            parts.append(f"{t.floors}층 {t.plate:.0f}㎡({lng:.1f}×{sht:.1f}m)")
        s = " + ".join(parts)
        if self.podium:
            lng, sht = self.podium.dims()
            s += (f" + 저층부 {self.podium.floors}층 {self.podium.plate:.0f}㎡"
                  f"({lng:.1f}×{sht:.1f}m)")
        return s


# --------------------------------------------------------------------------- #
# 법적 판정
# --------------------------------------------------------------------------- #
def facade_gap(site: Polygon, mass, samples: int = 9) -> float:
    """장변(채광창면) 직각 방향으로 대지경계선까지의 최단 수평거리.

    건축법 시행령 제86조 제3항 — 채광창이 있는 벽면 기준이다. 단변(측벽)은
    채광창이 없는 것으로 보아 제외한다. 기준면은 **외형선(발코니 끝)** 이다.
    """
    lng, sht = mass.dims()
    lng += 2 * BALCONY_M
    sht += 2 * BALCONY_M
    ux = math.sin(math.radians(mass.azimuth))
    uy = math.cos(math.radians(mass.azimuth))
    worst = float("inf")
    for sign in (1, -1):
        nz = math.radians(mass.azimuth + 90 * sign)
        nx, ny = math.sin(nz), math.cos(nz)
        for i in range(samples):
            t = i / (samples - 1) * lng - lng / 2
            px = mass.cx + ux * t + nx * sht / 2
            py = mass.cy + uy * t + ny * sht / 2
            ray = LineString([(px, py), (px + nx * 500, py + ny * 500)])
            inter = ray.intersection(site.exterior)
            if inter.is_empty:
                return 0.0
            pts = [inter] if inter.geom_type == "Point" else list(inter.geoms)
            d = min(Point(px, py).distance(p) for p in pts
                    if p.geom_type == "Point")
            worst = min(worst, d)
    return 0.0 if worst == float("inf") else worst


SIDE_WALL_GAP_M = 4.0       # 측벽과 측벽이 마주보는 경우
BLIND_WALL_GAP_M = 8.0      # 채광창 없는 벽면과 측벽이 마주보는 경우


def dong_gap(a, b, multiple: float) -> tuple[float, float, str]:
    """두 동 사이의 실제 수평거리와 법정 최소거리.

    건축법 시행령 제86조 제3항 제2호 — 같은 대지에서 두 동이 마주보는 경우
    **채광창이 있는 벽면**(여기선 장변)끼리면 각 부분 높이의 0.5배,
    측벽끼리면 4m, 채광창 없는 벽면과 측벽이면 8m 이상이다.

    마주봄의 판정은 **장변 방향으로 서로 겹치는지**로 한다 — 나란히 선 두
    판은 장변끼리 마주보고(0.5배), 일렬로 선 두 판은 측벽끼리 마주본다(4m).
    방위가 15°를 넘게 다르면 어느 쪽이든 장변이 상대를 보게 되므로 0.5배를
    적용한다(안전측).
    """
    oa, ob = a.outline(), b.outline()
    dist = oa.distance(ob)
    h = max(a.top_m(), b.top_m())
    if abs((a.azimuth - b.azimuth + 90) % 180 - 90) > 15.0:
        return dist, multiple * h, "장변 마주봄(방위 어긋남)"
    az = math.radians(a.azimuth)
    ux, uy = math.sin(az), math.cos(az)          # 장변 방향
    vx, vy = math.cos(az), -math.sin(az)         # 단변 방향
    def span(poly, px, py):
        t = [x * px + y * py for x, y in poly.exterior.coords]
        return min(t), max(t)
    au, bu = span(oa, ux, uy), span(ob, ux, uy)
    av, bv = span(oa, vx, vy), span(ob, vx, vy)
    if min(au[1], bu[1]) - max(au[0], bu[0]) > 0.0:
        return dist, multiple * h, "장변끼리 마주봄"
    if min(av[1], bv[1]) - max(av[0], bv[0]) > 0.0:
        return dist, SIDE_WALL_GAP_M, "측벽끼리 마주봄"
    return dist, SIDE_WALL_GAP_M, "대각 배치"


def dong_pairs(design: Design, multiple: float):
    """설계안의 동 쌍마다 (실제거리, 법정거리, 판정문구)."""
    ms = design.masses()
    for i in range(len(ms)):
        for j in range(i + 1, len(ms)):
            if design.podium is not None and not design.detached and (
                    ms[i] is design.podium or ms[j] is design.podium):
                continue                 # 타워가 저층부 위에 얹힌 경우는 한 동
            yield dong_gap(ms[i], ms[j], multiple)


def dong_gap_ok(design: Design, multiple: float, floor_m: float = 0.0) -> bool:
    """인동간격 판정.

    ``multiple`` 이 0 이면 배수 규정(0.5H)을 적용하지 않는다 — 소규모주택
    정비 특례로 완화를 받는 전제다. 그래도 **절대 하한**(``floor_m``)은
    남겨야 한다. 안 그러면 두 판을 0.2m 띄운 '한 덩어리'가 다동안으로
    뽑힌다(실제로 그런 일이 있었다).
    """
    for dist, need, _ in dong_pairs(design, multiple):
        if dist < max(need if multiple > 0.0 else 0.0, floor_m) - 1e-6:
            return False
    return True


def dong_gap_stats(design: Design) -> tuple[float, float]:
    """(최소 동간거리 m, 법정 0.5H 대비 비율). 1개동이면 (inf, inf)."""
    worst, ratio = float("inf"), float("inf")
    for dist, need, _ in dong_pairs(design, 0.5):
        worst = min(worst, dist)
        if need > 0:
            ratio = min(ratio, dist / need)
    return worst, ratio


def daylight_ratio(site: Polygon, design: Design) -> float:
    """채광 이격 여유율 = min(이격 x 배수 / 높이). 1.0 이상이면 충족."""
    worst = float("inf")
    for t in design.towers:
        gap = facade_gap(site, t)
        worst = min(worst, gap / t.top_m() if t.top_m() > 0 else 0.0)
    return worst


# --------------------------------------------------------------------------- #
# 준비
# --------------------------------------------------------------------------- #
def setup(args) -> dict[str, Any]:
    from pyproj import CRS, Transformer
    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    daegyo_new = H.load_daegyo(args.daegyo)
    site = H.build_site(buildings, args.site_area)
    d_centre = unary_union([p.footprint for p in daegyo_new]).centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(d_centre.x, d_centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)

    spec_all = SF.load_spec()
    schools = school_parcel_rows(buildings, d_centre, args.school_radius)
    all_b = unary_union([r["geom"] for r in buildings])
    points, receptors, grounds, dropped = build_points(
        schools, buildings, spec_all, all_b, args.apron, {}, args.pg_step)

    ctx_buildings = buildings
    sibeom_new: list[H.Prism] = []
    if getattr(args, "sibeom", None):
        # 시범도 신축안이 지어진 전제로 깐다. 시범 대지(지번 50)의 기존
        # 건물은 컨텍스트에서 빼야 신축안과 이중으로 가리지 않는다 —
        # apartment_attribution.py·3안 비교와 같은 구성이다.
        from apartment_attribution import SIBEOM_JIBUN, load_prisms_from_geojson
        sibeom_new = load_prisms_from_geojson(args.sibeom, "시범",
                                              layer_filter="신축동")
        if not sibeom_new:
            raise SystemExit(f"시범 신축 프리즘을 못 읽었다: {args.sibeom}")
        ctx_buildings = [r for r in buildings if r["jibun"] != SIBEOM_JIBUN]
    context, n_rec = build_context(ctx_buildings, d_centre, args.context_radius,
                                   spec_all)
    context = list(context) + list(daegyo_new)   # 대교 신축안은 지어진 전제
    context += sibeom_new                         # (--sibeom) 시범 신축안도
    masks = H.build_context_masks(context, receptors, times)
    base = H.evaluate(receptors, apartment_prisms(buildings, HWARANG_JIBUN,
                                                  "화랑 기존"),
                      times, masks, args.step_min)
    return {
        "site": site, "envelope": site.buffer(-args.setback),
        "times": times, "points": points, "receptors": receptors,
        "context": context, "masks": masks, "daegyo_new": daegyo_new,
        "base": base, "base_pass": [pass_a(r) for r in base],
        "n_dropped": len(dropped),
        "gfa": args.site_area * args.far / 100.0,
        "max_cover": args.site_area * args.bcr / 100.0,
    }


# --------------------------------------------------------------------------- #
# 후보 생성
# --------------------------------------------------------------------------- #
def grid_centres(envelope: Polygon, plate: float, aspect: float,
                 azimuth: float, step: float) -> list[tuple[float, float]]:
    minx, miny, maxx, maxy = envelope.bounds
    out = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if envelope.contains(plate_polygon(plate, aspect, azimuth, x, y,
                                               BALCONY_M)):
                out.append((round(x, 1), round(y, 1)))
            x += step
        y += step
    return out


def tower_short(plate: float, aspect: float) -> float:
    return math.sqrt(plate / aspect)


def tower_ok(plate: float, aspect: float) -> bool:
    if plate < MIN_TOWER_PLATE:
        return False
    return tower_short(plate, aspect) >= MIN_TOWER_SHORT


# --------------------------------------------------------------------------- #
# 탐색
# --------------------------------------------------------------------------- #
class Search:
    def __init__(self, ctx: dict[str, Any], args) -> None:
        self.ctx = ctx
        self.args = args
        self.rows: list[dict[str, Any]] = []
        self.seen: set[tuple] = set()
        self.n_eval = 0

    def key(self, d: Design) -> tuple:
        ts = tuple(sorted((round(t.plate), round(t.aspect, 2),
                           round(t.azimuth, 1), round(t.cx, 1), round(t.cy, 1),
                           t.floors) for t in d.towers))
        p = ((round(d.podium.plate), round(d.podium.aspect, 2),
              round(d.podium.azimuth, 1), round(d.podium.cx, 1),
              round(d.podium.cy, 1), d.podium.floors) if d.podium else None)
        return (d.family, ts, p, d.detached)

    def feasible(self, d: Design) -> bool:
        ctx = self.ctx
        if not (self.args.floors_min <= min(t.floors for t in d.towers)
                and d.top_floors <= self.args.floors_max):
            return False
        # 후보 생성식은 완전 포함을 전제로 기준층을 역산하므로, 가장자리가
        # 조금 내밀린 안은 연면적이 그만큼 커진다. 0.5%(188㎡)까지 받아 주고
        # 실제 용적률은 far_pct 열에 그대로 적는다.
        if abs(d.gfa_m2 - ctx["gfa"]) > max(1.0, 0.005 * ctx["gfa"]):
            return False
        if self.args.far_strict and d.gfa_m2 > ctx["gfa"] + 1.0:
            return False              # '용적률 400% 이내' — 넘는 안은 버린다
        for t in d.towers:
            if not tower_ok(t.plate, t.aspect):
                return False
        for _n, poly, _h in d.outlines():
            if not ctx["envelope"].contains(poly):
                return False
        if d.podium:
            pod = d.podium.outline()
            for t in d.towers:
                if d.detached:
                    if pod.intersects(t.outline()):
                        return False
                elif d.overlap_m2(t) < 0.97 * t.plate:
                    # 타워는 저층부 위에 얹혀야 한다. 다만 가장자리가 조금
                    # 내밀리는 건 캔틸레버로 성립하므로 3%까지 허용하고,
                    # 그만큼은 gfa_m2 가 타워 몫으로 정확히 되센다.
                    return False
        if len(d.towers) > 1:
            outs = [t.outline() for t in d.towers]
            for i in range(len(outs)):
                for j in range(i + 1, len(outs)):
                    if outs[i].intersects(outs[j]):
                        return False
        # 안 겹치는 것만으로는 2개동이 되지 않는다 — 법정 인동간격을 본다.
        # (이 검사가 없으면 0.2m 틈으로 갈라 놓은 한 덩어리가 '쌍둥이'로 뽑힌다)
        if not dong_gap_ok(d, self.args.dong_gap, self.args.min_dong_gap):
            return False
        if d.coverage_m2() > ctx["max_cover"]:
            return False
        if self.args.require_daylight and (
                daylight_ratio(ctx["site"], d) * self.args.daylight_multiple
                < 1.0):
            # 채광이격을 랭킹에 맡기면 탐색이 거의 안 간다 — 충족안만 보고
            # 싶을 땐 아예 걸러 낸다. 일조 계산 전이라 비용은 거의 없다.
            return False
        return True

    def evaluate(self, d: Design, stage: str) -> dict[str, Any] | None:
        k = self.key(d)
        if k in self.seen:
            return None
        self.seen.add(k)
        if not self.feasible(d):
            return None
        ctx = self.ctx
        res = H.evaluate(ctx["receptors"], d.prisms(), ctx["times"],
                         ctx["masks"], self.args.step_min)
        self.n_eval += 1
        nf = ng = 0
        for r, ok in zip(res, ctx["base_pass"]):
            now = pass_a(r)
            if ok and not now:
                nf += 1
            elif not ok and now:
                ng += 1
        row = {
            "family": d.family, "stage": stage,
            "floors": "+".join(str(t.floors) for t in d.towers),
            "top_floors": d.top_floors,
            "height_m": round(d.height_m, 2),
            "new_fail": nf, "new_ok": ng,
            "gfa_m2": round(d.gfa_m2), "far_pct": round(
                d.gfa_m2 / self.args.site_area * 100, 1),
            "service_m2": round(d.service_m2),
            "real_m2": round(d.real_m2),
            "coverage_m2": round(d.coverage_m2()),
            "bcr_pct": round(d.coverage_m2() / self.args.site_area * 100, 2),
            "daylight_ratio": round(daylight_ratio(ctx["site"], d), 3),
            "dong_gap_m": (None if len(d.masses()) < 2
                           else round(dong_gap_stats(d)[0], 1)),
            "dong_gap_ratio": (None if len(d.masses()) < 2
                               else round(dong_gap_stats(d)[1], 2)),
            "n_dong": len(d.masses()),
            "mean_total_h": round(
                sum(r["total_h_08_16"] for r in res) / len(res), 3),
            "min_short_m": round(min(t.dims()[1] for t in d.towers), 2),
            "label": d.label(),
            "_d": d, "_res": res,
        }
        self.rows.append(row)
        return row

    def sweep(self, tag: str, designs: Iterable[Design], budget: int
              ) -> list[dict[str, Any]]:
        """후보를 섞어 훑되, **실제로 평가된 수**가 예산에 닿을 때까지 돈다.

        후보 대부분은 배치 불가(대지 밖·중복·건폐율 초과)라 기하 검사에서
        걸러진다. 그건 몇 ms 면 끝나므로 예산에서 세지 않는다 — 예산은
        1초짜리 일조 계산에만 쓴다. 등간격(stride)으로 솎지 않고 섞는 이유는,
        중첩 반복문의 주기와 맞물려 특정 값만 계속 뽑히는 걸 막기 위해서다.
        """
        designs = list(designs)
        total = len(designs)
        random.Random(20260918).shuffle(designs)
        t0 = time.time()
        got: list[dict[str, Any]] = []
        tried = 0
        for d in designs:
            if len(got) >= budget:
                break
            tried += 1
            if (r := self.evaluate(d, tag)):
                got.append(r)
        best = min(got, key=rank, default=None)
        print(f"   {tag:<22} 후보 {total:>6} · 검토 {tried:>6} → 평가 {len(got):>4} "
              f"({time.time()-t0:>5.0f}s)"
              + (f"  최소 신규불충족 {best['new_fail']}" if best else "  (없음)"),
              flush=True)
        return got


RANK_MODE = "일조"


def rank(r: dict[str, Any]) -> tuple:
    """후보 정렬 기준.

    ``일조`` (기본)  ① 신규 불충족 최소 ② 층수 최대 ③ 평균 일조 최대
    ``건폐율``       ① 건폐율 최소 ② 신규 불충족 최소 ③ 평균 일조 최대

    층수 상한이 낮으면(예: 20층) 연면적이 고정이라 건폐율이 산술적으로 거의
    한 점에 묶인다. 그때는 건폐율을 1순위로 두어도 동률이 잔뜩 생기므로,
    2·3순위가 실제로 안을 고른다.
    """
    if RANK_MODE == "건폐율":
        return (round(r["bcr_pct"], 2), r["new_fail"], -r["mean_total_h"])
    return (r["new_fail"], -r["top_floors"], -r["mean_total_h"])


# --------------------------------------------------------------------------- #
def family_single(s: Search, azimuths, floors_list, budget: int
                  ) -> list[dict[str, Any]]:
    """단일 타워. 방위·위치를 먼저 훑고, 그 부근에서 층수·세장비를 훑는다."""
    ctx, args = s.ctx, s.args
    print("■ 갈래 1 — 단일 타워")
    # 탐침(1a)은 층수·세장비를 하나로 고정하고 방위·위치만 본다. 그 하나를
    # 상수로 박아 두면 층수 상한이 낮은 탐색(예: 20층 이하)에서 탐침 자체가
    # 성립하지 않아 후보가 0이 된다 — 반드시 실제 탐색 범위에서 고른다.
    floors_sorted = sorted(floors_list)
    probe_f = floors_sorted[len(floors_sorted) // 2]
    asp_sorted = sorted(args.aspects)
    probe_a = min(asp_sorted, key=lambda a: abs(
        a - asp_sorted[len(asp_sorted) // 2]))
    plate = ctx["gfa"] / probe_f
    if not tower_ok(plate, probe_a):
        # 탐침 세장비로는 단변 하한을 못 넘는 경우(층수가 아주 낮아 기준층이
        # 클 때는 반대로 세장비가 큰 쪽이 걸린다) 가능한 것 중 중앙값을 쓴다.
        ok = [a for a in asp_sorted if tower_ok(plate, a)]
        if not ok:
            print("   탐침 가능한 세장비가 없습니다 — 단일 갈래 건너뜀")
            return []
        probe_a = ok[len(ok) // 2]
    print(f"   탐침 {probe_f}층 · 기준층 {plate:,.0f}㎡ · 세장비 {probe_a:g}:1")
    a = s.sweep("1a 방위·위치", [
        Design("단일", (Tower(plate, probe_a, az, cx, cy, probe_f),))
        for az in azimuths
        for cx, cy in grid_centres(ctx["envelope"], plate, probe_a, az, 6.0)],
        budget // 3)
    if not a:
        return []
    b_designs = []
    for r in sorted(a, key=rank)[:10]:
        t0 = r["_d"].towers[0]
        for f in floors_list:
            p = ctx["gfa"] / f
            for asp in args.aspects:
                if not tower_ok(p, asp):
                    continue
                for daz in (-15.0, -7.5, 0.0, 7.5, 15.0):
                    az = (t0.azimuth + daz) % 180
                    for dx in (-6.0, 0.0, 6.0):
                        for dy in (-6.0, 0.0, 6.0):
                            b_designs.append(Design("단일", (Tower(
                                p, asp, az, t0.cx + dx, t0.cy + dy, f),)))
    b = s.sweep("1b 층수·세장비", b_designs, budget - budget // 3)
    return a + b


def family_podium(s: Search, base_rows, floors_list, budget: int,
                  tag="단일+저층부") -> list[dict[str, Any]]:
    """저층부(1~n층)로 연면적을 옮긴다. 저층부는 대지 장축에 맞춘다.

    저층부는 낮아 그림자가 짧다(5층 14.75m → 동지 정오 26.6m). 대신 넓어서
    가까운 학교(여의도여고 7.1m)에는 아침·저녁 저각 그림자가 닿는다. 그래서
    저층부 **위치**가 크기만큼 중요해 격자로 훑는다.
    """
    ctx, args = s.ctx, s.args
    print(f"■ 갈래 2 — {tag}")
    site_az, _ = H.site_axes(ctx["site"])
    # 타워 형상 후보는 단일 갈래의 우수해에서 가져온다(방위·세장비만 쓴다)
    shapes = []
    for r in sorted(base_rows, key=rank)[:10]:
        t = r["_d"].towers[0]
        k = (round(t.aspect, 2), round(t.azimuth, 1))
        if k not in shapes:
            shapes.append(k)
    designs = []
    for pf in args.podium_floors:
        for pplate in args.podium_plates:
            for pasp in (1.4, 1.8, 2.4, 3.2):
                for px, py in grid_centres(ctx["envelope"], pplate, pasp,
                                           site_az, 8.0):
                    pod = Podium(pplate, pasp, site_az, px, py, pf)
                    pod_out = pod.outline()
                    for f in floors_list:
                        if f <= pf:
                            continue
                        tp = (ctx["gfa"] - pplate * pf) / (f - pf)
                        if tp < MIN_TOWER_PLATE:
                            continue
                        for asp, az in shapes[:5]:
                            if not tower_ok(tp, asp):
                                continue
                            for cx, cy in grid_centres(pod_out, tp, asp, az,
                                                       9.0):
                                designs.append(Design(
                                    tag, (Tower(tp, asp, az, cx, cy, f),), pod))
    return s.sweep("2 저층부", designs, budget)


def family_multi(s: Search, base_rows, floors_list, n: int, budget: int
                 ) -> list[dict[str, Any]]:
    """n개동(n≥3). 같은 층수·같은 기준층으로 나눠 줄 세우거나 격자로 놓는다.

    연면적이 고정이라 동을 쪼갤수록 판이 얇아진다(4개동 20층이면 470㎡,
    세장비 4:1 에서 43.4×10.8m). 얇아진 판은 그림자 폭이 좁아 학교에
    유리하고, 둘레가 늘어 **발코니(서비스면적)가 크게 는다** — 대신 건축면적도
    같이 늘어 건폐율이 조금 올라간다. 그 맞바꿈을 보려고 넣은 갈래다.

    배치는 두 가지만 본다. 후보 수가 n 제곱으로 터지는 걸 막기 위해서다.
      · 줄배치  — 분리축 위에 같은 간격(pitch)으로 일렬
      · 격자배치 — n 이 짝수일 때 2줄 × (n/2)열
    """
    ctx, args = s.ctx, s.args
    tag = f"{n}개동"
    print(f"■ 갈래 — {tag}")
    site_az, _ = H.site_axes(ctx["site"])
    envelope = ctx["envelope"]
    ecx, ecy = envelope.centroid.x, envelope.centroid.y
    azimuths = sorted({round(site_az % 180, 1),
                       round((site_az + 90) % 180, 1)}
                      | {round(r["_d"].towers[0].azimuth, 1)
                         for r in sorted(base_rows, key=rank)[:6]})
    designs = []
    for f in floors_list:
        plate = ctx["gfa"] / (n * f)
        if plate < MIN_TOWER_PLATE:
            continue
        for asp in args.aspects:
            if not tower_ok(plate, asp):
                continue
            lng, sht = plate / math.sqrt(plate / asp), math.sqrt(plate / asp)
            top_m = f * RESI_FLOOR_H + ROOFTOP_M
            for az in azimuths:
                for row_off in (0.0, 30.0, 60.0, 90.0, 120.0, 150.0):
                    row_az = (az + row_off) % 180
                    # 줄 방향으로 본 외형선 폭. 이걸 알아야 최소 피치를
                    # 기하로 뽑을 수 있다 — 고정 목록으로 훑으면 맞는 값이
                    # 목록 사이에 빠져 후보가 통째로 0이 된다(실제로 그랬다).
                    th = math.radians(row_off)
                    extent = (abs(math.cos(th)) * (lng + 2 * BALCONY_M)
                              + abs(math.sin(th)) * (sht + 2 * BALCONY_M))
                    broadside = abs(((row_off + 90) % 180) - 90) > 75.0
                    need = max(args.min_dong_gap,
                               args.dong_gap * top_m if broadside
                               else SIDE_WALL_GAP_M)
                    pitch0 = extent + need
                    for dp in (0.3, 2.0, 5.0, 9.0, 15.0, 24.0):
                        pitch = pitch0 + dp
                        ux = math.sin(math.radians(row_az))
                        uy = math.cos(math.radians(row_az))
                        vx, vy = uy, -ux
                        # 줄 전체를 대지 안에서 옆으로도 밀어 본다
                        for sx in (-12.0, -6.0, 0.0, 6.0, 12.0):
                            for sy in (-12.0, 0.0, 12.0):
                                cx0 = ecx + vx * sx + ux * sy
                                cy0 = ecy + vy * sx + uy * sy
                                ts = tuple(
                                    Tower(plate, asp, az,
                                          cx0 + ux * (i - (n - 1) / 2) * pitch,
                                          cy0 + uy * (i - (n - 1) / 2) * pitch,
                                          f)
                                    for i in range(n))
                                designs.append(Design(tag, ts))
                        if n < 4 or n % 2:
                            continue
                        # 격자배치 — 2줄 x (n/2)열
                        cols = n // 2
                        pitch_c = extent + need
                        row_extent = (abs(math.sin(th)) * (lng + 2 * BALCONY_M)
                                      + abs(math.cos(th))
                                      * (sht + 2 * BALCONY_M))
                        gap2 = row_extent + max(
                            args.min_dong_gap,
                            args.dong_gap * top_m if not broadside
                            else SIDE_WALL_GAP_M) + dp
                        ts = tuple(
                            Tower(plate, asp, az,
                                  ecx + ux * (c - (cols - 1) / 2) * pitch_c
                                  + vx * r_ * gap2,
                                  ecy + uy * (c - (cols - 1) / 2) * pitch_c
                                  + vy * r_ * gap2, f)
                            for r_ in (-0.5, 0.5) for c in range(cols))
                        designs.append(Design(tag, ts))
    return s.sweep(f"{tag}", designs, budget)


def family_twin(s: Search, base_rows, floors_list, budget: int
                ) -> list[dict[str, Any]]:
    """2개동. 중심·분리축·간격·층수배분으로 잡아 후보 수를 억제한다.

    두 동을 **그림자 방향(남북)으로 겹쳐** 세우면 뒷동 그림자가 앞동 그림자
    안에 들어가 총 피해가 줄어들 수 있다. 반대로 동서로 벌리면 두 줄기가
    따로 훑는다. 그래서 분리축(sep_az)을 반드시 훑는다.
    """
    ctx, args = s.ctx, s.args
    print("■ 갈래 3 — 쌍둥이(2개동)")
    seeds = sorted(base_rows, key=rank)[:8]
    centres = []
    for r in seeds:
        t = r["_d"].towers[0]
        k = (round(t.cx, 0), round(t.cy, 0), round(t.azimuth, 0))
        if k not in centres:
            centres.append(k)
    designs = []
    for cx0, cy0, az in centres[:6]:
        for fa in floors_list:
            for fb in floors_list:
                if fb > fa:                      # (fa, fb) 대칭 중복 제거
                    continue
                for split in (0.5, 0.6, 0.7):    # 첫 동이 가져가는 연면적 비율
                    pa = ctx["gfa"] * split / fa
                    pb = ctx["gfa"] * (1 - split) / fb
                    for asp in (1.6, 2.2, 3.0, 4.0):
                        if not (tower_ok(pa, asp) and tower_ok(pb, asp)):
                            continue
                        for sep_az in (0.0, 30.0, 52.0, 90.0, 142.0):
                            # 법정 인동간격을 지키려면 꽤 벌려야 한다. 층수가
                            # 낮아 판이 커질수록 더 그렇다 — 넓게 훑는다.
                            for sep in (28.0, 38.0, 50.0, 62.0, 74.0, 86.0):
                                ux = math.sin(math.radians(sep_az)) * sep / 2
                                uy = math.cos(math.radians(sep_az)) * sep / 2
                                designs.append(Design("쌍둥이", (
                                    Tower(pa, asp, az, cx0 - ux, cy0 - uy, fa),
                                    Tower(pb, asp, az, cx0 + ux, cy0 + uy, fb))))
    return s.sweep("3 쌍둥이", designs, budget)


def family_twin_podium(s: Search, twin_rows, pod_rows, budget: int
                       ) -> list[dict[str, Any]]:
    """2개동 + 저층부.

    저층부는 앞 갈래에서 물려받지 않고 **여기서 직접 만든다.** 연면적이
    고정이라 저층부가 크면 남는 연면적을 둘로 쪼갠 타워 기준층이
    기준(300㎡) 밑으로 떨어져 아예 성립하지 않기 때문이다 — 예컨대
    저층부 7층 × 3,200㎡ 면 잔여 15,180㎡ 라 35층에서도 한 동이 271㎡ 다.
    앞 갈래의 우수해는 죄다 큰 저층부여서, 물려받으면 후보가 0이 된다.
    """
    ctx, args = s.ctx, s.args
    print("■ 갈래 4 — 쌍둥이+저층부")
    site_az, _ = H.site_axes(ctx["site"])
    azs, seen_az = [], set()
    for r in sorted(twin_rows or pod_rows, key=rank):
        a = round(r["_d"].towers[0].azimuth, 0)
        if a not in seen_az:
            seen_az.add(a)
            azs.append(a)
        if len(azs) >= 3:
            break
    if not azs:
        azs = [site_az]
    designs = []
    for pf in args.podium_floors:
        for pplate in args.podium_plates:
            rest = ctx["gfa"] - pplate * pf
            # 두 동으로 쪼갠 뒤에도 기준층이 남는 층수만 본다
            fs = [f for f in range(max(args.floors_min, pf + 5),
                                   args.floors_max + 1, 5)
                  if rest > 0 and rest * 0.5 / (f - pf) >= MIN_TOWER_PLATE]
            if not fs:
                continue
            for pasp in (1.4, 2.0, 3.0):
                for px, py in grid_centres(ctx["envelope"], pplate, pasp,
                                           site_az, 10.0):
                    pod = Podium(pplate, pasp, site_az, px, py, pf)
                    pod_out = pod.outline()
                    for f in fs:
                        for split in (0.5, 0.6):
                            pa = rest * split / (f - pf)
                            pb = rest * (1 - split) / (f - pf)
                            for asp in (1.6, 2.2, 3.0):
                                if not (tower_ok(pa, asp) and tower_ok(pb, asp)):
                                    continue
                                for az in azs:
                                    sa = grid_centres(pod_out, pa, asp, az, 10.0)
                                    sb = grid_centres(pod_out, pb, asp, az, 10.0)
                                    for cax, cay in sa:
                                        t1 = Tower(pa, asp, az, cax, cay, f)
                                        o1 = t1.outline()
                                        for cbx, cby in sb:
                                            if math.hypot(cbx - cax,
                                                          cby - cay) < 20.0:
                                                continue
                                            t2 = Tower(pb, asp, az, cbx, cby, f)
                                            if o1.intersects(t2.outline()):
                                                continue
                                            designs.append(Design(
                                                "쌍둥이+저층부", (t1, t2), pod))
    return s.sweep("4 쌍둥이+저층부", designs, budget)


def family_detached(s: Search, base_rows, floors_list, budget: int
                    ) -> list[dict[str, Any]]:
    """타워 + 저층 판상형 **별동**. 포디움과 달리 서로 떨어져 있다.

    포디움은 타워를 품어야 해서 타워 위치가 저층부 안으로 묶인다. 별동이면
    저층부를 학교에서 먼 남쪽에 몰아 두고 타워만 따로 세울 수 있어 배치
    자유도가 크다. 대신 저층부가 대지를 더 많이 차지해 건폐율이 빡빡해진다.
    """
    ctx, args = s.ctx, s.args
    print("■ 갈래 5 — 타워+저층별동")
    site_az, _ = H.site_axes(ctx["site"])
    shapes = []
    for r in sorted(base_rows, key=rank)[:10]:
        t = r["_d"].towers[0]
        k = (round(t.aspect, 2), round(t.azimuth, 1))
        if k not in shapes:
            shapes.append(k)
    designs = []
    for bf in args.podium_floors:
        for bplate in args.podium_plates:
            for basp in (2.0, 3.0, 4.5, 6.0):     # 판상형이라 길쭉하게
                for bx, by in grid_centres(ctx["envelope"], bplate, basp,
                                           site_az, 8.0):
                    blk = Podium(bplate, basp, site_az, bx, by, bf)
                    blk_out = blk.outline()
                    rest = ctx["gfa"] - bplate * bf
                    if rest <= 0:
                        continue
                    for f in floors_list:
                        tp = rest / f
                        if tp < MIN_TOWER_PLATE:
                            continue
                        for asp, az in shapes[:4]:
                            if not tower_ok(tp, asp):
                                continue
                            for cx, cy in grid_centres(ctx["envelope"], tp,
                                                       asp, az, 8.0):
                                t = Tower(tp, asp, az, cx, cy, f)
                                if blk_out.intersects(t.outline()):
                                    continue
                                designs.append(Design(
                                    "타워+저층별동", (t,), blk, detached=True))
    return s.sweep("5 저층별동", designs, budget)


def refine(s: Search, rows, budget: int, n_seeds: int = 3
           ) -> list[dict[str, Any]]:
    """상위 몇 개 안 부근을 촘촘히 훑는다(층수·세장비·방위·위치·저층부 위치)."""
    ctx, args = s.ctx, s.args
    seeds, seen_fam = [], set()
    for r in sorted(rows, key=rank):
        if r["family"] in seen_fam and len(seeds) >= n_seeds:
            continue
        seeds.append(r)
        seen_fam.add(r["family"])
        if len(seeds) >= n_seeds + 1:
            break
    designs = []
    for r in seeds:
        d0 = r["_d"]
        pf = d0.pod_floors
        rest = ctx["gfa"] - (d0.podium.plate * pf if d0.podium else 0.0)
        tot = sum(t.plate for t in d0.towers)
        for dfl in (-3, -2, -1, 0, 1, 2, 3):
            f = d0.top_floors + dfl
            if not (args.floors_min <= f <= args.floors_max) or f <= pf:
                continue
            for dasp in (0.85, 0.95, 1.0, 1.08, 1.2):
                for daz in (-8.0, -4.0, 0.0, 4.0, 8.0):
                    for dx in (-5.0, -2.5, 0.0, 2.5, 5.0):
                        for dy in (-5.0, -2.5, 0.0, 2.5, 5.0):
                            ts = tuple(
                                Tower(rest * (t.plate / tot) / (f - pf),
                                      t.aspect * dasp, (t.azimuth + daz) % 180,
                                      t.cx + dx, t.cy + dy, f)
                                for t in d0.towers)
                            designs.append(Design(d0.family, ts, d0.podium,
                                                  d0.detached))
    return s.sweep("5 미세조정", designs, budget)


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
def export(outdir: Path, row: dict[str, Any], ctx: dict[str, Any], args,
           stem: str) -> None:
    from pyproj import CRS, Transformer
    from school_receptor_compliance import (
        CHANGE_CODE, polygon_features, qml_style, write_geojson,
    )
    to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                  always_xy=True)
    outdir.mkdir(parents=True, exist_ok=True)
    d = row["_d"]
    items = [(n, poly, {"kind": n, "height_m": round(h, 2)})
             for n, poly, h in d.outlines()]
    items.append(("화랑 대지", ctx["site"],
                  {"kind": "대지", "area_m2": round(ctx["site"].area)}))
    massing = polygon_features(items)
    write_geojson(outdir / f"{stem}_매싱_epsg5186.geojson", massing)
    write_geojson(outdir / f"{stem}_매싱_wgs84.geojson", massing, to_wgs)

    feats = []
    for p, r, ok in zip(ctx["points"], row["_res"], ctx["base_pass"]):
        now = pass_a(r)
        chg = ("유지 충족" if ok and now else "신규 불충족" if ok and not now
               else "신규 충족" if now else "유지 불충족")
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [round(p.x, 3), round(p.y, 3)]},
                      "properties": {
                          "pid": p.pid, "school": p.school, "kind": p.kind,
                          "dong": p.dong, "floor": p.floor, "source": p.source,
                          "z_m": round(p.z, 2),
                          "base_pass_a": bool(ok), "plan_pass_a": bool(now),
                          "total_h": r["total_h_08_16"],
                          "cont_h": r["cont_h_08_16"],
                          "change_cd": CHANGE_CODE[chg], "변화": chg}})
    for name, sel in ((f"{stem}_수광점_전체", None),
                      (f"{stem}_수광점_신규불충족", "new_fail")):
        sub = [f for f in feats
               if sel is None or f["properties"]["change_cd"] == sel]
        a = outdir / f"{name}_epsg5186.geojson"
        write_geojson(a, sub)
        write_geojson(outdir / f"{name}_wgs84.geojson", sub, to_wgs)
        a.with_suffix(".qml").write_text(qml_style("change_cd"), encoding="utf-8")


def write_candidates(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    cols = [c for c in rows[0] if not c.startswith("_")]
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(cols)
        for r in sorted(rows, key=rank):
            w.writerow([r[c] for c in cols])


def school_table(ctx: dict[str, Any], row: dict[str, Any]) -> list[list[Any]]:
    """학교·동별 [구분, 수광점, 기준충족, 설계안충족, 신규불충족, 신규충족,
    평균일조변화]."""
    from collections import defaultdict
    agg: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0, 0, 0.0])
    for p, r, b, ok in zip(ctx["points"], row["_res"], ctx["base"],
                           ctx["base_pass"]):
        now = pass_a(r)
        a = agg[f"{p.school} / {p.dong}"]
        a[0] += 1
        a[1] += int(ok)
        a[2] += int(now)
        a[3] += int(ok and not now)
        a[4] += int(now and not ok)
        a[5] += r["total_h_08_16"] - b["total_h_08_16"]
    out = [[k, *v[:5], round(v[5] / v[0], 2)] for k, v in sorted(agg.items())]
    tot = [sum(r[i] for r in out) for i in range(1, 6)]
    out.append(["**전체**", *tot, round(
        sum(r["total_h_08_16"] for r in row["_res"])
        / len(row["_res"])
        - sum(r["total_h_08_16"] for r in ctx["base"]) / len(ctx["base"]), 2)])
    return out


def spec_rows(row: dict[str, Any], args) -> list[tuple[str, str]]:
    d = row["_d"]
    out = [("갈래", d.family), ("구성", d.label()),
           ("최고 층수", f"{row['top_floors']}층"),
           ("높이", f"{row['height_m']:,.1f} m")]
    for i, t in enumerate(d.towers):
        lng, sht = t.dims()
        out.append((f"타워{i+1} 기준층(연면적선)",
                    f"{t.plate:,.0f}㎡ ({lng:.1f}×{sht:.1f}m) · {t.floors}층 · "
                    f"방위 {t.azimuth:.0f}°"))
        out.append((f"타워{i+1} 중심",
                    f"E{t.cx:,.1f} / N{t.cy:,.1f}"))
    if d.podium:
        lng, sht = d.podium.dims()
        out.append(("저층부 기준층(연면적선)",
                    f"{d.podium.plate:,.0f}㎡ ({lng:.1f}×{sht:.1f}m) · "
                    f"{d.podium.floors}층 · 높이 {d.podium.top_m():.1f}m"))
        out.append(("저층부 중심",
                    f"E{d.podium.cx:,.1f} / N{d.podium.cy:,.1f}"))
    out += [
        ("연면적 (용적률 산정)", f"{row['gfa_m2']:,}㎡ · {row['far_pct']}%"),
        ("서비스면적 (발코니 1.5m)", f"{row['service_m2']:,}㎡"),
        ("**실면적 (연면적+서비스)**", f"**{row['real_m2']:,}㎡**"),
        ("건축면적 · 건폐율", f"{row['coverage_m2']:,}㎡ · {row['bcr_pct']}%"),
        ("채광이격 여유",
         f"{row['daylight_ratio'] * args.daylight_multiple:.2f}배 "
         f"({'충족' if row['daylight_ratio'] * args.daylight_multiple >= 1.0 else '미충족'})"),
        ("**신규 불충족**", f"**{row['new_fail']}개**"),
        ("신규 충족", f"{row['new_ok']}개"),
    ]
    return out


def write_report(path: Path, ctx, args, per_family, best, best_legal,
                 best_comfort, tiers, n_eval: int, nf0: int, ng0: int) -> None:
    L = [
        "# 화랑아파트 재건축 – 신규 불충족 최소 설계안 (다안 비교)",
        "",
        "## 설계 조건",
        "",
        "| 항목 | 값 |", "|---|---|",
        f"| 우선순위 | ① 교육환경평가 기준A **신규 불충족 최소** ② {args.floors_min}~{args.floors_max}층 "
        f"· 용적률 {args.far:g}% ③ 면적은 서비스면적 포함 ④ 저층부·2타워 등 다안 |",
        f"| 대지 | {ctx['site'].area:,.0f}㎡ (122.7m×76.5m, 장축 방위 52°) |",
        f"| 연면적 목표 | {ctx['gfa']:,.0f}㎡ (용적률 {args.far:g}%) |",
        f"| 건축면적 한도 | {ctx['max_cover']:,.0f}㎡ (건폐율 {args.bcr:g}%) |",
        f"| 그림자 형상 | **외형선 = 발코니 끝**(벽에서 {BALCONY_M}m). "
        "발코니는 연면적에서 빠지지만 그림자는 만든다 |",
        f"| 판정 | 동지({args.date}) 08~16시 {args.step_min}분 간격 · "
        f"기준A(연속 2h 이상 **또는** 총 4h 이상) |",
        f"| 수광점 | {len(ctx['receptors']):,}개 (건물 속에 박힌 점 "
        f"{ctx['n_dropped']}개 제외한 재점검본) |",
        f"| 기준선 | 대교 **신축안** + 화랑 **기존**(10층) → 충족 "
        f"{sum(ctx['base_pass']):,} / 불충족 "
        f"{len(ctx['base_pass'])-sum(ctx['base_pass']):,} |",
        "",
        f"철거 후 아무것도 짓지 않으면 신규 불충족 {nf0}개 · 신규 충족 {ng0}개다 — "
        "**신규 불충족의 하한은 0**이고, 짓는 순간 늘어난다. 아래 수치는 그 하한에서",
        "얼마나 떨어져 있는지로 읽으면 된다.",
        "",
        "## 갈래별 최우수안",
        "",
        "| 갈래 | 신규 불충족 | 신규 충족 | 최고층 | 높이 | 건폐율 | 실면적 | 채광이격 | 구성 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for f in FAMILIES:
        r = per_family.get(f)
        if not r:
            L.append(f"| {f} | — | — | — | — | — | — | — | 배치 가능한 해 없음 |")
            continue
        mark = "**" if r is best else ""
        dl = r["daylight_ratio"] * args.daylight_multiple
        L.append(
            f"| {mark}{f}{mark} | {mark}{r['new_fail']}{mark} | {r['new_ok']} | "
            f"{r['top_floors']}층 | {r['height_m']:,.1f}m | {r['bcr_pct']}% | "
            f"{r['real_m2']:,}㎡ | {dl:.2f}배 | {r['label']} |")
    L += ["", "## 최적안 제원", ""]
    L += ["| 항목 | 값 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in spec_rows(best, args)]
    if tiers:
        L += ["", "## 평면 형식(기준층 단변)별 최우수안", "",
              "타워를 얇게 할수록 그림자 폭이 좁아 학교에 유리하지만, 평면이",
              "불리해진다. 이건 설계 판단이라 어느 한쪽으로 정하지 않고 세 문턱의",
              "최우수안을 모두 낸다.", "",
              "| 단변 하한 | 평면 형식 | 신규 불충족 | 최고층 | 실 단변 | 구성 |",
              "|---:|---|---:|---:|---:|---|"]
        for thr, note, r in tiers:
            if r is None:
                L.append(f"| {thr:.1f}m | {note} | 해 없음 | — | — | — |")
            else:
                L.append(f"| {thr:.1f}m | {note} | **{r['new_fail']}** | "
                         f"{r['top_floors']}층 | {r['min_short_m']:.1f}m | "
                         f"{r['label']} |")
        L += ["",
              "단변 8.5m 는 세대깊이 7.2m + 복도 1.3m 로, 전용 50㎡대 소형 위주가",
              "된다. 11m 면 편복도형 표준, 14m 면 양면복도형이 되어 평면이 편해진다.",
              ""]
    if best_comfort is not None and best_comfort is not best:
        L += ["", "## 양면복도형(단변 14m 이상) 최우수안 제원", "",
              "| 항목 | 값 |", "|---|---|"]
        L += [f"| {k} | {v} |" for k, v in spec_rows(best_comfort, args)]

    L += ["", "## 최적안 – 학교·동별 전후", "",
          "| 학교 / 구분 | 수광점 | 기준 충족 | 설계안 충족 | 신규 불충족 | 신규 충족 | 평균 일조 변화 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for k, n, b, p_, nf, ng, dh in school_table(ctx, best):
        L.append(f"| {k} | {n} | {b} | {p_} | {nf} | {ng} | {dh:+.2f}h |")

    if best_legal is not None and best_legal is not best:
        L += ["", f"## 채광이격({args.daylight_multiple:g}배) 충족안", "",
              "최적안은 채광이격을 못 채운다. 이격을 법대로 지키는 후보 중 "
              "가장 나은 안은 아래와 같다.", "",
              "| 항목 | 값 |", "|---|---|"]
        L += [f"| {k} | {v} |" for k, v in spec_rows(best_legal, args)]
    elif best_legal is None:
        L += ["", f"## 채광이격({args.daylight_multiple:g}배)", "",
              "이 대지에서 용적률 400%·35층 이상을 채우면서 채광이격을 "
              "만족하는 배치는 **탐색 범위 안에 없었다.**",
              "대지 단변이 76.5m 뿐이라 한쪽 이격을 35m 이상 띄우려면 "
              "타워가 대지 밖으로 나간다.",
              "이격 완화(정비계획·지구단위계획)를 전제해야 한다 — "
              "종전 검토의 '트랙 A' 와 같은 전제다."]
    L += ["", "## 산출물", "",
          "| 파일 | 내용 |", "|---|---|",
          "| `최적안_매싱_*.geojson` | 최적안 외형선(발코니 끝)+대지 |",
          "| `최적안_수광점_전체_*.geojson` | 수광점 1,074개 전후 판정 |",
          "| `최적안_수광점_신규불충족_*.geojson` | 신규 불충족 점만 |",
          "| `갈래_*_매싱_*.geojson` | 갈래별 최우수안 |",
          "| `candidates.csv` | 평가한 후보 전량 |",
          "",
          f"평가 {n_eval:,}회. `python3 hwarang_design_2026.py "
          "--buildings <AL_D010.gpkg>` 로 재현한다.", ""]
    path.write_text("\n".join(L), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_2026"))
    p.add_argument("--cache", type=Path, default=None,
                   help="setup 결과 pickle 경로. 있으면 읽고 없으면 만든다")
    p.add_argument("--site-area", type=float, default=9395.0)
    p.add_argument("--far", type=float, default=400.0)
    p.add_argument("--bcr", type=float, default=60.0)
    p.add_argument("--setback", type=float, default=3.0)
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--pg-step", type=float, default=8.0)
    p.add_argument("--min-plate", type=float, default=MIN_TOWER_PLATE,
                   help="타워 기준층(연면적선) 하한 ㎡")
    p.add_argument("--min-short", type=float, default=MIN_TOWER_SHORT,
                   help="타워 기준층 단변 하한 m. 8.5=편복도 극단 / 11=편복도 / "
                        "14=양면복도")
    p.add_argument("--floors-min", type=int, default=35)
    p.add_argument("--floors-max", type=int, default=60)
    p.add_argument("--floors-step", type=int, default=5,
                   help="층수 후보 간격. 범위가 좁으면 1로 줄인다")
    p.add_argument("--aspects", type=float, nargs="*",
                   default=[1.5, 2.0, 2.5, 3.0, 4.0, 5.0],
                   help="타워 기준층 세장비(장변:단변) 후보. 판상형까지 보려면 "
                        "6~8을 더한다")
    p.add_argument("--rank", choices=["일조", "건폐율"], default="일조",
                   help="후보 정렬 1순위. 건폐율=최소 건폐율 우선")
    p.add_argument("--podium-floors", type=int, nargs="*", default=[3, 5, 7, 10])
    p.add_argument("--podium-plates", type=float, nargs="*",
                   default=[1600.0, 2400.0, 3200.0, 4000.0, 4800.0])
    p.add_argument("--sibeom", type=Path, default=None,
                   help="시범 신축안 GeoJSON. 주면 시범 기존 건물(지번 50)을 "
                        "컨텍스트에서 빼고 신축안을 깐다(대교·시범 신축 기준선)")
    p.add_argument("--ref", type=Path, nargs="*", default=None,
                   help="비교 기준 매싱 GeoJSON(예: 20층안). 같은 기준선에서 "
                        "신규 불충족을 계산해 함께 보고한다")
    p.add_argument("--far-strict", action="store_true",
                   help="연면적이 목표(용적률)를 1㎡라도 넘는 안은 버린다")
    p.add_argument("--floor-h", type=float, default=RESI_FLOOR_H,
                   help="주거 층고 m. 높이 = 층수 x 층고 + 옥탑 4m")
    p.add_argument("--towers", type=int, nargs="*", default=[1, 2],
                   help="탐색할 동 수. 예: 1 2 3 4")
    p.add_argument("--dong-gap", type=float, default=0.5,
                   help="동간거리 배수(건축법 시행령 86조 3항 2호, 장변끼리 "
                        "마주볼 때 높이의 0.5배). 0 이면 배수 규정을 빼고 "
                        "--min-dong-gap 만 본다(특례 완화 전제)")
    p.add_argument("--min-dong-gap", type=float, default=4.0,
                   help="동간거리 절대 하한 m. 배수 규정을 빼도 이건 남긴다")
    p.add_argument("--daylight-multiple", type=float, default=4.0,
                   help="채광 이격 배수(준주거 4배). 판정에만 쓰고 배제하지 않는다")
    p.add_argument("--require-daylight", action="store_true",
                   help="채광이격 미충족안을 후보에서 아예 뺀다. 탐색 예산을 "
                        "전부 충족안에 쓰고 싶을 때")
    p.add_argument("--budget", type=int, default=1800,
                   help="총 평가 예산(대략). 갈래별로 나눠 쓴다")
    p.add_argument("--no-podium", action="store_true",
                   help="저층부(포디움·별동) 갈래를 전부 빼고 순수 고층 "
                        "단일/쌍둥이만 예산을 몰아서 훑는다")
    p.add_argument("--az-step", type=float, default=15.0,
                   help="1a 단계 방위 스캔 간격(도). 예산이 넉넉하면 좁힌다")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    global MIN_TOWER_PLATE, MIN_TOWER_SHORT, RANK_MODE, RESI_FLOOR_H
    args = parse_args(argv)
    MIN_TOWER_PLATE, MIN_TOWER_SHORT = args.min_plate, args.min_short
    RANK_MODE = args.rank
    RESI_FLOOR_H = args.floor_h
    args.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if args.cache and args.cache.exists():
        ctx = pickle.loads(args.cache.read_bytes())
        # 차폐 마스크는 prepared geometry 라 절일 수 없다 — 다시 만든다.
        ctx["masks"] = H.build_context_masks(ctx["context"], ctx["receptors"],
                                             ctx["times"])
        print(f"■ 준비(캐시) {time.time()-t0:.0f}s")
    else:
        ctx = setup(args)
        if args.cache:
            args.cache.write_bytes(pickle.dumps(
                {k: v for k, v in ctx.items() if k != "masks"}))
        print(f"■ 준비 {time.time()-t0:.0f}s")

    base_pass = ctx["base_pass"]
    print(f"   수광점 {len(ctx['receptors'])}개 · 기준(대교신축+화랑기존) "
          f"충족 {sum(base_pass)} / 불충족 {len(base_pass)-sum(base_pass)}")
    print(f"   대지 {ctx['site'].area:,.0f}㎡ · 연면적 목표 {ctx['gfa']:,.0f}㎡ "
          f"· 건축면적 한도 {ctx['max_cover']:,.0f}㎡")
    none_res = H.evaluate(ctx["receptors"], [], ctx["times"], ctx["masks"],
                          args.step_min)
    nf0 = sum(1 for r, ok in zip(none_res, base_pass) if ok and not pass_a(r))
    ng0 = sum(1 for r, ok in zip(none_res, base_pass) if not ok and pass_a(r))
    print(f"   참고(철거·미신축): 신규 불충족 {nf0} / 신규 충족 {ng0}")
    refs = []
    for rp in args.ref or []:
        from apartment_attribution import load_prisms_from_geojson
        pr = load_prisms_from_geojson(rp, "참고")
        rr = H.evaluate(ctx["receptors"], pr, ctx["times"], ctx["masks"],
                        args.step_min)
        rnf = sum(1 for r, ok in zip(rr, base_pass) if ok and not pass_a(r))
        rng = sum(1 for r, ok in zip(rr, base_pass) if not ok and pass_a(r))
        refs.append((rp, rnf, rng, max(p.top_m for p in pr)))
        print(f"   참고안 {rp.parent.name}: 신규 불충족 {rnf} / 신규 충족 {rng} "
              f"(최고 {max(p.top_m for p in pr):.1f}m)")
    args.ref_results = refs
    print()

    s = Search(ctx, args)
    n_az = max(4, round(180.0 / args.az_step))
    azimuths = [a * args.az_step for a in range(n_az)]
    floors_list = list(range(args.floors_min, args.floors_max + 1,
                             max(1, args.floors_step)))
    if args.floors_max not in floors_list:
        floors_list.append(args.floors_max)

    B = args.budget
    csv_path = args.outdir / "candidates.csv"

    def save() -> None:
        if s.rows:
            write_candidates(csv_path, s.rows)

    if args.no_podium:
        # 저층부를 아예 금지 — 순수 고층 단일/쌍둥이만 본다. 저층부 갈래가
        # 먹던 예산을 전부 이쪽에 몰아주고, 미세조정도 세 번 돈다.
        ns = sorted({n for n in args.towers if n >= 1})
        print(f"■ 저층부 금지 모드 — 동 수 {ns} 만 탐색")
        # 단일 갈래는 늘 먼저 돌린다. 다른 동 수의 방위·위치 씨앗이 여기서
        # 나오기 때문이다(1개동이 탐색 목록에 없어도 마찬가지다).
        single = family_single(s, azimuths, floors_list, int(B * 0.24))
        save()
        allrows = list(single) if 1 in ns else []
        share = 0.44 / max(1, len([n for n in ns if n >= 2]))
        for n in ns:
            if n < 2:
                continue
            # 2개동도 family_multi 로 보낸다. family_twin 의 고정 간격 격자는
            # 층수가 낮아 판이 커지면 맞는 간격이 목록 사이에 빠져 후보가
            # 거의 안 남는다(2,160개 중 11개). family_multi 는 최소 간격을
            # 기하로 뽑으므로 그 구멍이 없다.
            got = family_multi(s, single, floors_list, n, int(B * share))
            allrows += got
            save()
        if not allrows:
            allrows = list(single)
        if not allrows:
            raise SystemExit("배치 가능한 후보가 없습니다.")
        allrows += refine(s, allrows, int(B * 0.16), n_seeds=6)
        save()
        allrows += refine(s, allrows, int(B * 0.09), n_seeds=4)
        save()
        allrows += refine(s, allrows, int(B * 0.07), n_seeds=3)
        save()
    else:
        # 1차 탐색에서 쌍둥이(2개동)가 단일보다 뚜렷이 나빠(49 대 34) 예산을
        # 줄이고 저층부 계열에 몰아 준다.
        # 종전 검토('zero-newfail' 42층+저층부)를 씨앗으로 먼저 넣는다. 탐색이
        # 그보다 못한 해를 내놓는 일이 없도록 하는 안전판이고, 미세조정 단계가
        # 그 부근을 다시 훑게 하는 출발점이기도 하다.
        prev = [Design("단일+저층부",
                       (Tower(299.4, 3.906, 166.5, 194309.2, 546962.7, 42),),
                       Podium(2800.0, 1.6, 52.0, 194295.2, 546944.7, 10))]
        seeded = s.sweep("0 종전안", prev, 5)
        single = family_single(s, azimuths, floors_list, int(B * 0.13)) + seeded
        save()
        pod = family_podium(s, single, floors_list, int(B * 0.24))
        save()
        det = family_detached(s, single, floors_list, int(B * 0.22))
        save()
        twin = family_twin(s, single, floors_list, int(B * 0.08))
        save()
        twinpod = family_twin_podium(s, twin, pod, int(B * 0.16))
        save()
        allrows = single + pod + det + twin + twinpod
        if not allrows:
            raise SystemExit("배치 가능한 후보가 없습니다.")
        # 미세조정은 두 번 돈다 — 1차로 좋아진 해를 씨앗으로 다시 좁힌다.
        allrows += refine(s, allrows, int(B * 0.11), n_seeds=4)
        save()
        allrows += refine(s, allrows, int(B * 0.06), n_seeds=2)
        save()

    # 갈래별 최우수
    print("\n" + "=" * 92)
    print("■ 갈래별 최우수안")
    print("=" * 92)
    hdr = (f"{'갈래':<14}{'신규불충족':>8}{'신규충족':>8}{'최고층':>7}"
           f"{'높이m':>8}{'건폐율':>8}{'실면적㎡':>10}{'채광이격':>9}  제원")
    print(hdr)
    print("-" * 92)
    per_family: dict[str, dict[str, Any]] = {}
    for r in allrows:
        f = r["family"]
        if f not in per_family or rank(r) < rank(per_family[f]):
            per_family[f] = r
    for f in FAMILIES:
        r = per_family.get(f)
        if not r:
            continue
        ok = "충족" if r["daylight_ratio"] * args.daylight_multiple >= 1.0 \
            else f"{r['daylight_ratio']*args.daylight_multiple:.2f}배"
        print(f"{f:<14}{r['new_fail']:>8}{r['new_ok']:>8}{r['top_floors']:>7}"
              f"{r['height_m']:>8.1f}{r['bcr_pct']:>7.1f}%{r['real_m2']:>10,}"
              f"{ok:>9}  {r['label']}")

    tiers: list[tuple[float, str, dict[str, Any] | None]] = []
    for thr, note in DEPTH_TIERS:
        sub = [r for r in allrows if r["min_short_m"] >= thr - 1e-6]
        tiers.append((thr, note, min(sub, key=rank) if sub else None))
    print("\n■ 기준층 단변(평면 형식)별 최우수안 — 좁을수록 그림자가 좁다")
    print(f"{'단변 하한':>9}{'평면형식':>14}{'신규불충족':>9}{'최고층':>7}"
          f"{'실단변m':>9}  제원")
    print("-" * 92)
    for thr, note, r in tiers:
        if r is None:
            print(f"{thr:>8.1f}m{note:>14}{'해 없음':>9}")
            continue
        print(f"{thr:>8.1f}m{note:>14}{r['new_fail']:>9}{r['top_floors']:>7}"
              f"{r['min_short_m']:>9.1f}  {r['label']}")
    best_comfort = tiers[-1][2]

    legal = [r for r in allrows
             if r["daylight_ratio"] * args.daylight_multiple >= 1.0]
    best_legal = min(legal, key=rank) if legal else None
    if best_legal:
        print(f"\n■ 채광이격({args.daylight_multiple:g}배) 충족 후보 중 최우수 — "
              f"신규 불충족 {best_legal['new_fail']}개 · {best_legal['top_floors']}층")
        print(f"   {best_legal['label']}")
    else:
        print(f"\n■ 채광이격({args.daylight_multiple:g}배)을 충족하는 후보는 "
              "하나도 없다 — 이격 완화를 전제해야 한다")

    best = min(allrows, key=rank)
    print("\n" + "=" * 92)
    print(f"■ 최적안 — {best['family']}")
    print("=" * 92)
    print(f"   {best['label']}")
    print(f"   신규 불충족 {best['new_fail']}개 · 신규 충족 {best['new_ok']}개")
    print(f"   연면적 {best['gfa_m2']:,}㎡(용적률 {best['far_pct']}%) + "
          f"서비스 {best['service_m2']:,}㎡ = 실면적 {best['real_m2']:,}㎡")
    print(f"   건축면적 {best['coverage_m2']:,}㎡(건폐율 {best['bcr_pct']}%) · "
          f"높이 {best['height_m']}m")
    print(f"   채광이격 여유 {best['daylight_ratio']*args.daylight_multiple:.2f}배"
          " (1.00 이상이면 충족)")

    export(args.outdir, best, ctx, args, "최적안")
    for f, r in per_family.items():
        if r is not best:
            export(args.outdir, r, ctx, args, f"갈래_{f}")
    if best_legal and best_legal is not best:
        export(args.outdir, best_legal, ctx, args, "채광이격충족안")
    if best_comfort and best_comfort is not best:
        export(args.outdir, best_comfort, ctx, args, "양면복도형안")
    write_report(args.outdir / "report.md", ctx, args, per_family, best,
                 best_legal, best_comfort, tiers, s.n_eval, nf0, ng0)

    save()
    (args.outdir / "best.json").write_text(json.dumps(
        {k: v for k, v in best.items() if not k.startswith("_")},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n평가 {s.n_eval}회 · {time.time()-t0:.0f}s → {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
