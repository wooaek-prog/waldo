#!/usr/bin/env python3
"""화랑 A안 대지 프로그램 — 어린이집(유치원)·성큰광장·주차램프·산책로·조경 기하.

배치도(hwarang_plans_A.py)와 3D 상세 모형(hwarang_tower_detail.py)이 같은
기하를 쓰도록 한곳에 둔다. 좌표는 타워 중심 원점(E194,327.70 N546,974.70,
EPSG:5186) 기준 x=동, y=북, m.

대지 좌표(a, b) — 대지는 76.5 × 122.7m 의 기운 직사각형이다.
  a : 남동 변(0) → 북서 변(76.5)      방향 e1 = 남쪽 모서리 → 서쪽 모서리
  b : 남서 변(0) → 북동 변(122.7)     방향 e2 = 남쪽 모서리 → 동쪽 모서리
타워는 대지 북동부를 대각으로 가로지른다(a 4–65, b 67–113). 넓은 빈 땅은
남서부(b < 60)다 — 어린이집과 성큰광장을 여기에 둔다.

일조: 어린이집(지상 2층, 높이 8.0m)을 남서부 어디에 두어도 A안의 신규
불충족(건물 33 · 전체 73)이 하나도 늘지 않음을 확인했다(30개 위치 검사,
hwarang_plans_A.py --check-sun 로 다시 확인 가능).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

ENVELOPE = Path("outputs/hwarang_hr_bld_nopod_2026/최적안_매싱_epsg5186.geojson")
ORIGIN = (194327.70, 546974.70)

KG_FLOORS, KG_FLOOR_H, KG_PARAPET = 2, 3.6, 0.8      # 어린이집 2층, 높이 8.0m
KG_HEIGHT = KG_FLOORS * KG_FLOOR_H + KG_PARAPET
SUNKEN_DEPTH = 5.0                                  # 성큰 바닥 = B1 FL −5.0
STAND_TIERS = 10
STAIR_STEPS = 33                                    # 직통계단 단높이 0.15m


@dataclass
class SiteFrame:
    S: np.ndarray        # 남쪽 모서리(로컬 xy)
    e1: np.ndarray       # a 방향
    e2: np.ndarray       # b 방향
    A: float
    B: float

    def xy(self, a, b):
        p = self.S + a * self.e1 + b * self.e2
        return float(p[0]), float(p[1])

    def ab(self, x, y):
        d = np.array([x, y]) - self.S
        return float(d @ self.e1), float(d @ self.e2)

    def rect(self, a0, a1, b0, b1) -> Polygon:
        return Polygon([self.xy(a, b) for a, b in
                        ((a0, b0), (a1, b0), (a1, b1), (a0, b1))])

    def line(self, pts) -> LineString:
        return LineString([self.xy(a, b) for a, b in pts])


def load_site() -> tuple[Polygon, Polygon]:
    """(대지, 타워 외형선) — 타워 중심 원점 로컬 좌표."""
    feats = json.loads(ENVELOPE.read_text(encoding="utf-8"))["features"]
    from shapely import affinity
    site = next(shape(f["geometry"]) for f in feats
                if f["properties"].get("kind") == "대지")
    outline = next(shape(f["geometry"]) for f in feats
                   if f["properties"].get("height_m"))
    return (affinity.translate(site, -ORIGIN[0], -ORIGIN[1]),
            affinity.translate(outline, -ORIGIN[0], -ORIGIN[1]))


def site_frame(site: Polygon) -> SiteFrame:
    pts = [np.array(c) for c in list(site.exterior.coords)[:-1]]
    i = min(range(len(pts)), key=lambda k: pts[k][1])        # 남쪽 모서리
    S = pts[i]
    nb = [pts[i - 1], pts[(i + 1) % len(pts)]]
    west = min(nb, key=lambda p: p[0])
    east = max(nb, key=lambda p: p[0])
    e1, e2 = west - S, east - S
    A, B = np.linalg.norm(e1), np.linalg.norm(e2)
    return SiteFrame(S, e1 / A, e2 / B, float(A), float(B))


def rounded(p: Polygon, r: float) -> Polygon:
    return p.buffer(-r, join_style="mitre").buffer(r, quad_segs=6)


def program() -> dict:
    """대지 프로그램 기하(로컬 xy). 면적은 shapely 로 잰다."""
    site, outline = load_site()
    F = site_frame(site)
    tower_zone = outline                         # 일조 외형선(발코니 포함)

    kg = rounded(F.rect(50.0, 64.0, 6.0, 32.0), 3.0)             # 어린이집
    kg_yard = F.rect(27.0, 47.5, 6.0, 32.0)                      # 전용 놀이마당
    kg_entry = F.rect(64.0, 66.0, 14.0, 22.0)                    # 산책로 쪽 주출입
    sunken = rounded(F.rect(18.0, 46.0, 40.0, 58.0), 2.0)        # 성큰광장
    # 성큰: 북동쪽 계단식 스탠드(10단, 단높이 0.5m · 깊이 0.55m) + 남동쪽 직통계단
    stand = [F.rect(21.5, 46.0, 58.0 - (k + 1) * 0.55, 58.0 - k * 0.55)
             for k in range(STAND_TIERS)]
    stair = F.rect(18.0, 21.5, 40.0, 52.0)
    b1_front = F.line([(46.0, 40.0), (46.0, 58.0)])              # B1 카페·도서관 유리면
    ramp = F.rect(3.0, 10.0, 96.0, 121.0)                        # 지하주차장 진출입
    path = F.line([(69.0, 0.0), (67.5, 40.0), (70.5, 96.0), (70.0, F.B)])
    path_poly = path.buffer(1.5, cap_style="flat")
    # 타워 서측 로비 앞 광장: 로비(서측 아케이드)와 산책로·성큰을 잇는다
    plaza = (F.rect(14.0, 70.0, 40.0, 104.0)
             .intersection(site.buffer(-3.0))
             .difference(tower_zone.buffer(0.5)))
    plaza = unary_union([plaza.intersection(
        Polygon(unary_union([tower_zone.buffer(14.0), sunken.buffer(4.0)])
                .convex_hull.exterior)), sunken.buffer(4.0)])
    hard = unary_union([plaza, path_poly, kg_entry, ramp.buffer(1.0),
                        kg.buffer(1.5), tower_zone.buffer(1.0)])
    lawn = (site.buffer(-3.0).difference(hard).difference(kg_yard.buffer(1.0)))
    lawn = unary_union([g for g in getattr(lawn, "geoms", [lawn])
                        if g.area > 40])

    # 수목: 대지 둘레 8m 간격 + 잔디 가장자리 수림대
    trees = []
    ring = site.buffer(-2.0).exterior
    n = int(ring.length // 8)
    keep_out = unary_union([tower_zone.buffer(10.0), ramp.buffer(3.0),
                            path_poly.buffer(1.0), kg.buffer(3.0),
                            sunken.buffer(3.0), kg_yard.buffer(0.5)])
    for k in range(n):
        p = ring.interpolate(k * ring.length / n)
        if not keep_out.contains(p):
            trees.append((p.x, p.y, 7.0))
    rng = np.random.default_rng(7)
    for g in getattr(lawn, "geoms", [lawn]):
        x0, y0, x1, y1 = g.bounds
        for x in np.arange(x0 + 4, x1, 8.5):
            for y in np.arange(y0 + 4, y1, 8.5):
                p = Point(x + rng.uniform(-2.5, 2.5), y + rng.uniform(-2.5, 2.5))
                if (g.buffer(-2.0).contains(p) and g.boundary.distance(p) < 7.0
                        and not keep_out.contains(p)
                        and min((Point(t[0], t[1]).distance(p) for t in trees),
                                default=99) > 5.0):
                    trees.append((p.x, p.y, round(float(rng.uniform(5.5, 8.0)), 1)))
    # 놀이마당 그늘목 3그루(남쪽 가장자리)
    for a, b in ((29.5, 12.0), (29.5, 20.0), (29.5, 28.0)):
        x, y = F.xy(a, b)
        trees.append((x, y, 6.0))

    return {
        "frame": F, "site": site, "outline": outline,
        "kg": kg, "kg_yard": kg_yard, "kg_entry": kg_entry,
        "sunken": sunken, "stand": stand, "stair": stair, "b1_front": b1_front,
        "ramp": ramp, "path": path, "path_poly": path_poly,
        "plaza": plaza, "lawn": lawn, "trees": trees,
    }


if __name__ == "__main__":
    P = program()
    F = P["frame"]
    print(f"대지 {P['site'].area:,.0f}㎡ · a {F.A:.1f}m × b {F.B:.1f}m")
    for k in ("kg", "kg_yard", "sunken", "ramp", "plaza", "lawn"):
        g = P[k]
        print(f"  {k:8s} {g.area:8.1f}㎡  대지안 {P['site'].buffer(1e-6).contains(g)}"
              f"  타워외형선과 겹침 {g.intersection(P['outline']).area:.1f}")
    print(f"  수목 {len(P['trees'])}그루")
