#!/usr/bin/env python3
"""화랑 A안 — 조감도용 정밀 폴리곤(상세 매스) 생성.

A안(51층 1개동, 저층부 없음)의 확정 매스를 **유엔스튜디오(UNStudio) 레퍼런스의
어휘**로 분절한 상세 모형을 만든다. 참조한 어휘:

  · 적층 블록의 엇갈림(stacked & shifted blocks) — 3개 블록이 장축 방향으로
    ±0.8m 씩 엇갈려 쌓인다(참조: 쌍둥이 타워 투시도, EZ Parque da Cidade)
  · 픽셀형 발코니(pixelated balconies) — 서측면 발코니 깊이가 베이·층마다
    0 / 0.85 / 1.35m 로 바뀌며 블록마다 사선 패턴이 달라진다(쌍둥이 타워 좌측동)
  · 핀 입면(fin-clad glass) — 동측면은 연속 얕은 발코니 + 수직 핀, 단부는
    유리 + 수직 핀(쌍둥이 타워 우측동)
  · 깎인 모서리 스카이가든 — 블록 경계층(18·35층)의 대각 모서리를 파내
    녹화 테라스(Palas Residential, 쌍둥이 타워)
  · 수평 슬래브 밴드와 둥근 모서리 — 층마다 슬래브 선이 돌고 평면 모서리를
    둥글린다(Kyklos Building)
  · 열린 프레임 크라운 — 옥상에 핀 프레임(퍼걸러)과 옥상정원, 최상 2개층은
    단부·모서리를 물려 테라스(쌍둥이 타워 상부, Kyklos 지붕)
  · 필로티형 아케이드 로비 — 1층 유리벽을 서측 2m·단부 1.5m 물리고 2층
    슬래브가 캐노피가 된다

**불변 조건** — 일조 분석을 다시 하지 않아도 되도록:
  1. 모든 요소가 분석에 쓴 외형선 각기둥(62.47 × 15.39m, 높이 172.3m)
     **안에** 있다 → 그림자는 분석값보다 작거나 같다(건물 신규 불충족 ≤ 33).
     스크립트가 모든 꼭짓점을 검사해 벗어나면 멈춘다.
  2. 연면적 = 37,580㎡(용적률 400.0%) — 층별 판 면적을 합해 장변 길이를
     이분탐색으로 맞춘다.
  3. 51층 · 층고 3.3m · 옥탑(크라운) 4.0m — 최고 172.3m.

산출(outputs/hwarang_detail_A_2026/)
  화랑A_상세모형.obj/.mtl · .glb     타워+조경. 원점 = 타워 중심
                                     (E194,327.70 N546,974.70, EPSG:5186),
                                     x=동 y=북 z=위, m — `조감_모형.obj` 와 같은 원점
  화랑A_상세+주변.obj/.mtl           위 + 주변 건물 화이트 모델(대교·시범·학교·기존)
  화랑A_층별연면적선_*.geojson       층별 연면적선(유리선) 폴리곤 + 층·높이·면적
  화랑A_발코니_*.geojson             발코니 판 폴리곤 + 층·깊이
  화랑A_조경_*.geojson               스카이가든·옥상·지상 조경 폴리곤, 수목 점
  화랑A_층별면적표.csv               층별 연면적·발코니 면적
  (렌더링은 hwarang_render_A.py)

    python3 hwarang_tower_detail.py --buildings <AL_D010.gpkg>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import (LineString, MultiPolygon, Point, Polygon, box,
                              mapping, shape)
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

# --------------------------------------------------------------------------- #
# 확정 조건
# --------------------------------------------------------------------------- #
ENVELOPE = Path("outputs/hwarang_hr_bld_nopod_2026/최적안_매싱_epsg5186.geojson")
PLAYGROUNDS = Path("outputs/school_compliance/playground/운동장_배치도경계_epsg5186.geojson")
FLOORS = 51
FLOOR_H = 3.3
CROWN_H = 4.0
GFA_TARGET = 37_580.0
SLAB_T = 0.35          # 슬래브 두께
SLAB_EDGE = 0.15       # 유리선 밖으로 나오는 슬래브 밴드
W_PLATE = 12.6         # 판 깊이(단변) — 발코니 1.35m 를 외형선 안에 넣는 최대치
R_CORNER = 2.0         # 평면 모서리 반경
SHIFT = 0.8            # 블록 엇갈림(장축 방향)
BAL_LEVELS = (1.35, 0.85, 0.0)   # 서측 픽셀 발코니 깊이
BAL_BAYS = 17
EAST_BAND = 0.6        # 동측 연속 발코니
EAST_FIN = (0.62, 1.12)    # 동측 핀 v 범위(판 면에서)
END_FIN = 0.3          # 단부 핀 깊이
RAIL_H = 1.1
MULLION = (0.06, 0.12, 1.5)   # 커튼월 멀리언 폭·돌출·간격

# 층 구성: (층 범위, 블록, 장축 이동)
BLOCKS = {"A": (range(3, 18), -SHIFT), "B": (range(19, 35), +SHIFT),
          "C": (range(36, 50), -SHIFT)}
SKY = {18: ("NW", "SE"), 35: ("NE", "SW")}      # 스카이가든 — 파내는 모서리
CARVE_LEN = 8.0
CROWN1_CARVE = ("NW", "SE")                      # 50층
CROWN2_TRIM = 4.0                                 # 51층 양 단부 물림

MATERIALS = {   # 이름: (RGB 0~1, 설명)
    "glass":    ((0.46, 0.55, 0.62), "유리 커튼월"),
    "slab":     ((0.89, 0.85, 0.78), "슬래브 밴드(샴페인 화이트)"),
    "balcony":  ((0.92, 0.89, 0.83), "발코니 판"),
    "soffit":   ((0.69, 0.51, 0.35), "발코니·캐노피 하부(목재 톤)"),
    "railing":  ((0.70, 0.78, 0.82), "유리 난간"),
    "fin":      ((0.82, 0.74, 0.60), "수직 핀(브론즈 샴페인)"),
    "mullion":  ((0.74, 0.70, 0.63), "커튼월 멀리언"),
    "frame":    ((0.86, 0.80, 0.70), "크라운 프레임"),
    "column":   ((0.88, 0.86, 0.82), "로비 기둥"),
    "planter":  ((0.62, 0.59, 0.54), "플랜터"),
    "green":    ((0.36, 0.52, 0.30), "식재"),
    "trunk":    ((0.40, 0.30, 0.22), "수간"),
    "paving":   ((0.80, 0.78, 0.74), "대지 포장"),
    "lawn":     ((0.52, 0.64, 0.40), "잔디"),
    "context":  ((0.93, 0.93, 0.91), "주변 건물(화이트 모델)"),
    "context_new": ((0.88, 0.90, 0.93), "대교·시범 신축안(화이트 모델)"),
    "school":   ((0.93, 0.88, 0.86), "학교"),
    "ground":   ((0.86, 0.86, 0.83), "주변 지면"),
    "field":    ((0.47, 0.60, 0.40), "학교 운동장(인조잔디)"),
    "track":    ((0.66, 0.40, 0.32), "학교 운동장 트랙"),
}


# --------------------------------------------------------------------------- #
# 좌표계 — u=장축(방위 az), v=동쪽 직교축, 원점 = 타워 중심
# --------------------------------------------------------------------------- #
@dataclass
class Frame:
    cx: float
    cy: float
    az: float
    L_env: float
    W_env: float

    @property
    def u(self):
        a = math.radians(self.az)
        return math.sin(a), math.cos(a)

    @property
    def v(self):   # u 를 시계 반대로 90° 돌린 축 — 회전(행렬식 +1)이 되게 잡는다
        a = math.radians(self.az)
        return -math.cos(a), math.sin(a)

    def to_local(self, u, v):
        (ux, uy), (vx, vy) = self.u, self.v
        return u * ux + v * vx, u * uy + v * vy

    def poly(self, p: Polygon) -> Polygon:
        (ux, uy), (vx, vy) = self.u, self.v
        return affinity.affine_transform(p, [ux, vx, uy, vy, 0.0, 0.0])

    def to_world(self, p):
        return affinity.translate(p, self.cx, self.cy)


def load_frame(path: Path) -> tuple[Frame, Polygon, Polygon]:
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    outline = next(shape(f["geometry"]) for f in feats
                   if f["properties"].get("height_m"))
    site = next(shape(f["geometry"]) for f in feats
                if f["properties"].get("kind") == "대지")
    pts = list(outline.minimum_rotated_rectangle.exterior.coords)[:4]
    edges = []
    for i in range(4):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 4]
        edges.append((math.hypot(x1 - x0, y1 - y0),
                      math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180))
    L, az = max(edges)
    W = min(e[0] for e in edges)
    c = outline.centroid
    return Frame(c.x, c.y, az, L, W), outline, site


# --------------------------------------------------------------------------- #
# 메시
# --------------------------------------------------------------------------- #
@dataclass
class Mesh:
    verts: list = field(default_factory=list)
    faces: dict = field(default_factory=dict)      # mat -> [(i,j,k)]
    groups: dict = field(default_factory=dict)     # mat -> [group id]
    _g: int = 0

    def _v(self, p):
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts) - 1

    def new_group(self):
        self._g += 1
        return self._g

    def tri(self, mat, a, b, c, g):
        ia, ib, ic = self._v(a), self._v(b), self._v(c)
        self.faces.setdefault(mat, []).append((ia, ib, ic))
        self.groups.setdefault(mat, []).append(g)

    def polygon_cap(self, poly: Polygon, z: float, mat: str, up: bool):
        g = self.new_group()
        tris = shapely.constrained_delaunay_triangles(poly)
        for t in getattr(tris, "geoms", [tris]):
            if t.is_empty:
                continue
            (x0, y0), (x1, y1), (x2, y2) = list(t.exterior.coords)[:3]
            ccw = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0) > 0
            a, b, c = (x0, y0, z), (x1, y1, z), (x2, y2, z)
            if ccw == up:
                self.tri(mat, a, b, c, g)
            else:
                self.tri(mat, a, c, b, g)

    def walls(self, poly: Polygon, z0: float, z1: float, mat: str):
        for ring in [poly.exterior, *poly.interiors]:
            pts = list(ring.coords)
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if math.hypot(x1 - x0, y1 - y0) < 1e-6:
                    continue
                g = self.new_group()
                a0, b0 = (x0, y0, z0), (x1, y1, z0)
                a1, b1 = (x0, y0, z1), (x1, y1, z1)
                self.tri(mat, a0, b0, b1, g)
                self.tri(mat, a0, b1, a1, g)

    def prism(self, poly, z0, z1, mat, top=None, bottom=None, caps=True):
        for p in explode(poly):
            if p.area < 1e-4:
                continue
            p = orient(p, sign=1.0)
            self.walls(p, z0, z1, mat)
            if caps:
                self.polygon_cap(p, z1, top or mat, up=True)
                self.polygon_cap(p, z0, bottom or mat, up=False)

    def box4(self, q, z0, z1, mat):
        """반시계 네 점 q 의 각기둥 — shapely 없이 빠르게(멀리언용)."""
        for k in range(4):
            (x0, y0), (x1, y1) = q[k], q[(k + 1) % 4]
            g = self.new_group()
            self.tri(mat, (x0, y0, z0), (x1, y1, z0), (x1, y1, z1), g)
            self.tri(mat, (x0, y0, z0), (x1, y1, z1), (x0, y0, z1), g)
        a, b, c, d = [(x, y, z1) for x, y in q]
        g = self.new_group()
        self.tri(mat, a, b, c, g)
        self.tri(mat, a, c, d, g)
        a, b, c, d = [(x, y, z0) for x, y in q]
        g = self.new_group()
        self.tri(mat, a, c, b, g)
        self.tri(mat, a, d, c, g)

    def ico(self, cx, cy, cz, r, mat, sz=1.0):
        t = (1 + 5 ** 0.5) / 2
        vs = [(-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0), (0, -1, t), (0, 1, t),
              (0, -1, -t), (0, 1, -t), (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1)]
        n = math.sqrt(1 + t * t)
        vs = [(cx + x / n * r, cy + y / n * r, cz + z / n * r * sz) for x, y, z in vs]
        fs = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9),
              (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8), (3, 9, 4), (3, 4, 2),
              (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10),
              (8, 6, 7), (9, 8, 1)]
        for a, b, c in fs:
            self.tri(mat, vs[a], vs[b], vs[c], self.new_group())

    def all_vertices(self) -> np.ndarray:
        return np.asarray(self.verts, dtype=float)


def explode(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


# --------------------------------------------------------------------------- #
# 설계
# --------------------------------------------------------------------------- #
def rr(L: float, W: float, r: float, du: float = 0.0, dv: float = 0.0) -> Polygon:
    """uv 둥근 사각형(연면적선)."""
    b = box(-L / 2, -W / 2, L / 2, W / 2).buffer(-r, join_style="mitre")
    return affinity.translate(b.buffer(r, quad_segs=6), du, dv)


def corner_box(L, W, which, length, du=0.0):
    """uv 모서리 파냄 상자. N=−u(북), S=+u(남), W=−v(서), E=+v(동)."""
    us = -1 if which[0] == "N" else 1
    vs = -1 if which[1] == "W" else 1
    u_in, u_out = us * (L / 2 - length), us * (L / 2 + 2)
    v_in, v_out = 0.0, vs * (W / 2 + 2)
    return box(min(u_in, u_out) + du, min(v_in, v_out),
               max(u_in, u_out) + du, max(v_in, v_out))


def floor_spec(i: int):
    """층 i → (종류, 장축 이동)."""
    if i == 1:
        return "lobby", 0.0
    if i == 2:
        return "base", 0.0
    for name, (rng, s) in BLOCKS.items():
        if i in rng:
            return name, s
    if i in SKY:
        return "sky", 0.0
    if i == 50:
        return "crown1", 0.0
    if i == 51:
        return "crown2", 0.0
    raise ValueError(i)


def plate(i: int, L: float) -> Polygon:
    kind, s = floor_spec(i)
    full = rr(L, W_PLATE, R_CORNER, s)
    if kind == "lobby":        # 서측 2m 아케이드, 단부 1.5m 물림
        return rr(L - 3.0, W_PLATE - 2.0, R_CORNER, 0.0, +1.0)
    if kind == "sky":
        return full.difference(unary_union([corner_box(L, W_PLATE, c, CARVE_LEN)
                                            for c in SKY[i]]))
    if kind == "crown1":
        return full.difference(unary_union([corner_box(L, W_PLATE, c, 6.0)
                                            for c in CROWN1_CARVE]))
    if kind == "crown2":
        return rr(L - 2 * CROWN2_TRIM, W_PLATE, R_CORNER)
    return full


def solve_length() -> tuple[float, list[float]]:
    lo, hi = 50.0, 62.0
    for _ in range(60):
        mid = (lo + hi) / 2
        tot = sum(plate(i, mid).area for i in range(1, FLOORS + 1))
        lo, hi = (mid, hi) if tot < GFA_TARGET else (lo, mid)
    L = (lo + hi) / 2
    return L, [plate(i, L).area for i in range(1, FLOORS + 1)]


def z_of(i: int) -> float:
    return (i - 1) * FLOOR_H


def build_tower(fr: Frame, L: float, mesh: Mesh, gis: dict) -> None:
    P = fr.poly
    plates = {i: plate(i, L) for i in range(1, FLOORS + 1)}
    W = W_PLATE

    # 층별 유리 볼륨 + 슬래브
    for i in range(1, FLOORS + 1):
        z0, z1 = z_of(i), z_of(i + 1)
        pl = plates[i]
        mesh.prism(P(pl), z0 + (SLAB_T if i > 1 else 0.0), z1, "glass",
                   caps=False)
        below = plates.get(i - 1)
        slab = pl if below is None else unary_union([pl, below])
        slab = slab.buffer(SLAB_EDGE, quad_segs=6)
        mesh.prism(P(slab), z0, z0 + SLAB_T, "slab", bottom="soffit")
        gis["floors"].append((i, P(pl), z0, z1, pl.area, floor_spec(i)[0]))
    # 커튼월 멀리언 — 유리선 밖 0.12m, 약 1.5m 간격(둥근 모서리 포함)
    mw, md, mstep = MULLION
    for i in range(1, FLOORS + 1):
        z0, z1 = z_of(i) + (SLAB_T if i > 1 else 0.0), z_of(i + 1)
        for pl in explode(plates[i]):
            ring = orient(pl, 1.0).exterior
            n = max(4, int(round(ring.length / mstep)))
            for k in range(n):
                s = k * ring.length / n
                a, b = ring.interpolate(s - 0.05), ring.interpolate(s + 0.05)
                tx, ty = b.x - a.x, b.y - a.y
                t = math.hypot(tx, ty)
                tx, ty = tx / t, ty / t
                nx, ny = ty, -tx                       # 반시계 외곽의 바깥 법선
                c = ring.interpolate(s)
                q = [(c.x - tx * mw / 2, c.y - ty * mw / 2),
                     (c.x - tx * mw / 2 + nx * md, c.y - ty * mw / 2 + ny * md),
                     (c.x + tx * mw / 2 + nx * md, c.y + ty * mw / 2 + ny * md),
                     (c.x + tx * mw / 2, c.y + ty * mw / 2)]
                mesh.box4([fr.to_local(u, v) for u, v in q], z0, z1, "mullion")
    # 지붕 슬래브(51층 위)
    zr = z_of(FLOORS + 1)
    mesh.prism(P(plates[FLOORS].buffer(SLAB_EDGE, quad_segs=6)), zr, zr + SLAB_T,
               "slab", bottom="soffit")

    # 로비 기둥(서측 아케이드) — 2층 슬래브를 받친다
    for k in range(-4, 5):
        u = k * 6.5
        c = Point(u, -W / 2 + 0.6).buffer(0.35, quad_segs=3)
        mesh.prism(P(c), 0.0, FLOOR_H, "column")

    # 서측 픽셀 발코니 / 동측 연속 발코니
    straight = L - 2 * R_CORNER
    bay = straight / BAL_BAYS
    phase = {"A": (1, 1), "B": (2, 1), "C": (1, 2), "base": (1, 0)}
    for i in range(2, FLOORS + 1):
        kind, s = floor_spec(i)
        if kind not in phase:
            continue
        z0 = z_of(i)
        ka, kb = phase[kind]
        for b in range(BAL_BAYS):
            d = BAL_LEVELS[(ka * b + kb * i) % 3]
            if d <= 0:
                continue
            u0 = -straight / 2 + b * bay + s + 0.06
            u1 = u0 + bay - 0.12
            bp = box(u0, -W / 2 - d, u1, -W / 2)
            add_balcony(mesh, P, bp, z0, "W")
            gis["balconies"].append((i, P(bp), z0, d, "서측 픽셀"))
        bp = box(-straight / 2 + s, W / 2, straight / 2 + s, W / 2 + EAST_BAND)
        add_balcony(mesh, P, bp, z0, "E")
        gis["balconies"].append((i, P(bp), z0, EAST_BAND, "동측 연속"))

    # 블록별 수직 핀(동측·단부)
    for name, (rng, s) in BLOCKS.items():
        zb, zt = z_of(rng.start), z_of(rng.stop)
        n = int(straight // 1.5)
        for k in range(n + 1):
            u = -straight / 2 + k * straight / n + s
            f = box(u - 0.06, W / 2 + EAST_FIN[0], u + 0.06, W / 2 + EAST_FIN[1])
            mesh.prism(P(f), zb, zt, "fin")
        for sign in (-1, 1):
            ue = sign * (L / 2) + s
            for k in range(6):
                v = -W / 2 + R_CORNER + k * (W - 2 * R_CORNER) / 5
                f = box(min(ue, ue + sign * END_FIN), v - 0.05,
                        max(ue, ue + sign * END_FIN), v + 0.05)
                mesh.prism(P(f), zb, zt, "fin")

    # 스카이가든·크라운 테라스 조경
    for i in list(SKY) + [50, 51]:
        z0 = z_of(i) + SLAB_T
        terrace = plates[i - 1].intersection(
            rr(L, W, R_CORNER, floor_spec(i - 1)[1])).difference(plates[i])
        for t in explode(terrace):
            if t.area < 4:
                continue
            ring = t.buffer(-0.2).difference(t.buffer(-1.0))
            mesh.prism(P(ring), z0, z0 + 0.9, "planter", top="green")
            gis["landscape"].append((f"{i}층 테라스 식재", P(ring), z0))
            c = t.buffer(-1.6)
            if not c.is_empty:
                p = c.representative_point()
                x, y = fr.to_local(p.x, p.y)
                add_tree(mesh, x, y, z0 + 0.9, 3.2, 1.3)
                gis["trees"].append((x, y, z0 + 0.9, 3.2))

    # 크라운 프레임(퍼걸러) + 옥상정원
    zr1 = zr + SLAB_T
    top = z_of(FLOORS + 1) + CROWN_H
    outer = rr(L + 0.6, W + 0.6, R_CORNER + 0.3)
    inner = rr(L - 0.6, W - 0.6, R_CORNER - 0.3)
    mesh.prism(P(outer.difference(inner)), top - 0.4, top, "frame")
    n = int((L - 2 * R_CORNER) // 1.2)
    for sign in (-1, 1):
        for k in range(n + 1):
            u = -(L - 2 * R_CORNER) / 2 + k * (L - 2 * R_CORNER) / n
            f = box(u - 0.075, sign * (W / 2) - 0.3, u + 0.075, sign * (W / 2) + 0.3)
            mesh.prism(P(f), zr1, top - 0.4, "frame")
    roof_green = rr(L - 2 * CROWN2_TRIM - 3, W - 4, 1.0)
    for g in explode(roof_green.difference(box(-6, -3, 6, 3))):
        mesh.prism(P(g), zr1, zr1 + 0.6, "planter", top="green")
        gis["landscape"].append(("옥상정원", P(g), zr1))
    for u in (-14.0, -7.0, 7.0, 14.0):
        x, y = fr.to_local(u, 0.0)
        add_tree(mesh, x, y, zr1 + 0.6, 2.4, 0.9)
        gis["trees"].append((x, y, zr1 + 0.6, 2.4))


def add_balcony(mesh: Mesh, P, bp: Polygon, z0: float, side: str) -> None:
    mesh.prism(P(bp), z0 + 0.1, z0 + SLAB_T, "balcony", bottom="soffit")
    u0, v0, u1, v1 = bp.bounds
    t = 0.04
    zr0, zr1 = z0 + SLAB_T, z0 + SLAB_T + RAIL_H
    outer_v = v0 if side == "W" else v1
    rails = [box(u0, outer_v - (0 if side == "W" else t),
                 u1, outer_v + (t if side == "W" else 0))]
    if side == "W":
        rails += [box(u0, v0, u0 + t, v1), box(u1 - t, v0, u1, v1)]
    for r in rails:
        mesh.prism(P(r), zr0, zr1, "railing")


def add_tree(mesh: Mesh, x, y, z, h, r):
    trunk = Point(x, y).buffer(0.15, quad_segs=1)
    mesh.prism(trunk, z, z + h * 0.55, "trunk")
    mesh.ico(x, y, z + h * 0.55 + r * 0.7, r, "green", sz=0.9)


def build_landscape(fr: Frame, site_w: Polygon, mesh: Mesh, gis: dict,
                    outline_w: Polygon) -> None:
    site = affinity.translate(site_w, -fr.cx, -fr.cy)
    outline = affinity.translate(outline_w, -fr.cx, -fr.cy)
    mesh.prism(site, -0.3, 0.02, "paving")
    lobby_side = fr.poly(box(-40, -30, 40, -6.3))     # 서측 전면 광장
    lawn = (site.buffer(-3.5)
            .difference(outline.buffer(9))
            .difference(lobby_side.intersection(outline.buffer(26))))
    for g in explode(lawn):
        if g.area < 30:
            continue
        mesh.prism(g, 0.02, 0.25, "lawn", top="lawn")
        gis["landscape"].append(("지상 잔디·식재", g, 0.02))
    # 잔디 가장자리 수림대 — 가운데는 열린 잔디로 남긴다
    rng = np.random.default_rng(7)
    for g in explode(lawn):
        if g.area < 30:
            continue
        x0, y0, x1, y1 = g.bounds
        for x in np.arange(x0 + 4, x1, 9.0):
            for y in np.arange(y0 + 4, y1, 9.0):
                p = Point(x + rng.uniform(-2.5, 2.5), y + rng.uniform(-2.5, 2.5))
                if (g.buffer(-2.5).contains(p) and g.boundary.distance(p) < 8.0
                        and outline.distance(p) > 12):
                    h = rng.uniform(5.5, 8.0)
                    add_tree(mesh, p.x, p.y, 0.25, h, h * 0.33)
                    gis["trees"].append((p.x, p.y, 0.25, round(h, 1)))
    ring = site.buffer(-2.2).exterior
    n = int(ring.length // 8)
    for k in range(n):
        p = ring.interpolate(k * ring.length / n)
        if outline.distance(p) < 14:
            continue
        add_tree(mesh, p.x, p.y, 0.02, 7.0, 2.4)
        gis["trees"].append((p.x, p.y, 0.02, 7.0))


# --------------------------------------------------------------------------- #
# 주변 건물(화이트 모델)
# --------------------------------------------------------------------------- #
def build_context(args, fr: Frame, mesh: Mesh) -> int:
    import hwarang_birdseye_2026 as BE
    ns = argparse.Namespace(buildings=args.buildings, hwarang=ENVELOPE,
                            daegyo=args.daegyo, sibeom=args.sibeom,
                            radius=args.radius, school_radius=350.0)
    solids, _rows, _c = BE.build_scene(ns)
    n = 0
    for s in solids:
        if s.kind == "hwarang":
            continue
        mat = {"daegyo": "context_new", "sibeom": "context_new",
               "school": "school"}.get(s.kind, "context")
        p = affinity.translate(s.footprint, -fr.cx, -fr.cy)
        mesh.prism(p, 0.0, s.top_m, mat)
        n += 1
    # 학교 운동장(배치도 해치 경계) — 트랙 4m 띠 + 안쪽 필드
    if PLAYGROUNDS.exists():
        for f in json.loads(PLAYGROUNDS.read_text(encoding="utf-8"))["features"]:
            pg = affinity.translate(shape(f["geometry"]), -fr.cx, -fr.cy)
            inner = pg.buffer(-4.0, join_style="mitre")
            mesh.prism(pg.difference(inner), -0.3, 0.04, "track")
            mesh.prism(inner, -0.3, 0.03, "field")
    g = box(-args.radius - 150, -args.radius - 150, args.radius + 150,
            args.radius + 150)
    step = 50.0
    xs = np.arange(g.bounds[0], g.bounds[2], step)
    for x in xs:
        for y in xs:
            mesh.polygon_cap(box(x, y, x + step, y + step), -0.35, "ground", up=True)
    return n


# --------------------------------------------------------------------------- #
# 내보내기
# --------------------------------------------------------------------------- #
def write_obj(mesh: Mesh, path: Path, fr: Frame, note: str) -> None:
    mtl = path.with_suffix(".mtl")
    with mtl.open("w", encoding="utf-8") as fp:
        for k, (rgb, desc) in MATERIALS.items():
            if k not in mesh.faces:
                continue
            d = 0.55 if k in ("glass", "railing") else 1.0
            fp.write(f"# {desc}\nnewmtl {k}\nKd {rgb[0]:.3f} {rgb[1]:.3f} {rgb[2]:.3f}\n"
                     f"Ka 0 0 0\nKs 0.1 0.1 0.1\nd {d}\nillum 2\n\n")
    lines = [f"# {note}",
             f"# 원점 E{fr.cx:.2f} N{fr.cy:.2f} (EPSG:5186) · x=동 y=북 z=위 · m",
             f"mtllib {mtl.name}"]
    for x, y, z in mesh.verts:
        lines.append(f"v {x:.3f} {y:.3f} {z:.3f}")
    for mat, fs in mesh.faces.items():
        lines.append(f"g {mat}")
        lines.append(f"usemtl {mat}")
        for a, b, c in fs:
            lines.append(f"f {a + 1} {b + 1} {c + 1}")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_glb(mesh: Mesh, path: Path) -> bool:
    try:
        import trimesh
    except ImportError:
        return False
    V = mesh.all_vertices()
    parts = []
    for mat, fs in mesh.faces.items():
        F = np.asarray(fs)
        used = np.unique(F)
        remap = -np.ones(len(V), dtype=int)
        remap[used] = np.arange(len(used))
        # glTF 는 Y-up — (x, z, −y) 로 돌린다
        vv = V[used][:, [0, 2, 1]] * np.array([1, 1, -1])
        rgb = MATERIALS[mat][0]
        alpha = 0.55 if mat in ("glass", "railing") else 1.0
        m = trimesh.Trimesh(vv, remap[F], process=False)
        m.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                name=mat, baseColorFactor=[*rgb, alpha],
                metallicFactor=0.3 if mat in ("glass", "fin", "frame") else 0.0,
                roughnessFactor=0.2 if mat == "glass" else 0.7,
                alphaMode="BLEND" if alpha < 1 else "OPAQUE"))
        parts.append((mat, m))
    scene = trimesh.Scene()
    for mat, m in parts:
        scene.add_geometry(m, node_name=mat, geom_name=mat)
    scene.export(str(path))
    return True


def write_geojsons(outdir: Path, fr: Frame, gis: dict) -> None:
    from pyproj import CRS, Transformer
    tw = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                              always_xy=True)

    def world(g):
        return fr.to_world(g)

    def dump(name, feats):
        for crs, tf in (("epsg5186", None), ("wgs84", tw)):
            fs = []
            for f in feats:
                g = f["geometry"]
                if tf is not None:
                    g = shapely.ops.transform(lambda x, y, z=None: tf.transform(x, y), g)
                fs.append({"type": "Feature", "geometry": mapping(g),
                           "properties": f["properties"]})
            doc = {"type": "FeatureCollection", "features": fs}
            if tf is None:
                doc["crs"] = {"type": "name", "properties":
                              {"name": "urn:ogc:def:crs:EPSG::5186"}}
            (outdir / f"{name}_{crs}.geojson").write_text(
                json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    dump("화랑A_층별연면적선", [
        {"geometry": world(p), "properties": {
            "floor": i, "z_bottom_m": round(z0, 2), "z_top_m": round(z1, 2),
            "height_m": round(z1 - z0, 2), "area_m2": round(a, 2), "kind": k}}
        for i, p, z0, z1, a, k in gis["floors"]])
    dump("화랑A_발코니", [
        {"geometry": world(p), "properties": {
            "floor": i, "z_m": round(z0, 2), "depth_m": d, "type": t,
            "area_m2": round(p.area, 2)}}
        for i, p, z0, d, t in gis["balconies"]])
    feats = [{"geometry": world(p), "properties": {"type": n, "z_m": round(z, 2)}}
             for n, p, z in gis["landscape"]]
    feats += [{"geometry": world(Point(x, y)), "properties": {
        "type": "수목", "z_m": round(z, 2), "height_m": h}}
        for x, y, z, h in gis["trees"]]
    dump("화랑A_조경", feats)


# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--buildings", type=Path, default=None,
                    help="AL_D010 gpkg — 주면 주변 건물 화이트 모델도 함께 낸다")
    ap.add_argument("--daegyo", type=Path,
                    default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    ap.add_argument("--sibeom", type=Path,
                    default=Path("outputs/sibeom/sibeom_epsg5186.geojson"))
    ap.add_argument("--radius", type=float, default=420.0)
    ap.add_argument("--outdir", type=Path,
                    default=Path("outputs/hwarang_detail_A_2026"))
    a = ap.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)

    fr, outline_w, site_w = load_frame(ENVELOPE)
    L, areas = solve_length()
    gfa = sum(areas)
    print(f"외형선 {fr.L_env:.2f}×{fr.W_env:.2f}m · 방위 {fr.az:.1f}° · "
          f"중심 E{fr.cx:.2f} N{fr.cy:.2f}")
    print(f"판 장변 {L:.3f}m × 단변 {W_PLATE}m (모서리 R{R_CORNER}) · "
          f"연면적 {gfa:,.1f}㎡ · 용적률 {gfa / 9395 * 100:.2f}%")

    gis = {"floors": [], "balconies": [], "landscape": [], "trees": []}
    tower = Mesh()
    build_tower(fr, L, tower, gis)
    n_tower = sum(len(f) for f in tower.faces.values())

    # 불변 조건 1 — 분석 외형선 각기둥 안
    V = tower.all_vertices()
    env_local = affinity.translate(outline_w, -fr.cx, -fr.cy).buffer(1e-3)
    inside = shapely.contains_xy(env_local, V[:, 0], V[:, 1])
    top = z_of(FLOORS + 1) + CROWN_H
    if not inside.all() or V[:, 2].max() > top + 1e-6:
        bad = V[~inside]
        raise SystemExit(f"외형선을 벗어난 꼭짓점 {len(bad)}개 · 최고 "
                         f"{V[:, 2].max():.2f}m — 일조 분석이 무효가 된다")
    print(f"검사: 꼭짓점 {len(V):,}개 전부 분석 외형선 안 · 최고 {V[:, 2].max():.2f}m")

    site_land = Mesh()
    build_landscape(fr, site_w, site_land, gis, outline_w)
    model = Mesh()
    for src in (tower, site_land):
        off = len(model.verts)
        model.verts += src.verts
        for mat, fs in src.faces.items():
            model.faces.setdefault(mat, []).extend(
                [(x + off, y + off, z + off) for x, y, z in fs])
            model.groups.setdefault(mat, []).extend(
                [g + model._g for g in src.groups[mat]])
        model._g += src._g + 1
    write_obj(model, a.outdir / "화랑A_상세모형.obj", fr,
              "화랑 A안 상세 모형(타워+대지 조경)")
    glb = write_glb(model, a.outdir / "화랑A_상세모형.glb")

    # 층별 면적표·발코니
    bal = {}
    for i, p, z0, d, t in gis["balconies"]:
        bal[i] = bal.get(i, 0.0) + p.area
    with (a.outdir / "화랑A_층별면적표.csv").open("w", encoding="utf-8-sig",
                                              newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["층", "구분", "바닥높이_m", "층고_m", "연면적_㎡", "발코니_㎡"])
        for i, p, z0, z1, ar, k in gis["floors"]:
            w.writerow([i, k, round(z0, 2), FLOOR_H, round(ar, 2),
                        round(bal.get(i, 0.0), 2)])
        w.writerow(["합계", "", "", "", round(gfa, 2),
                    round(sum(bal.values()), 2)])
    write_geojsons(a.outdir, fr, gis)

    # 건축면적(참고): 판들의 수평투영 합집합 + 발코니 1m 초과분
    proj = unary_union([p for _, p, *_ in gis["floors"]])
    over = unary_union([p.difference(proj.buffer(1.0))
                        for _, p, *_ in gis["balconies"]])
    bcr = (proj.area + over.area) / 9395 * 100
    print(f"발코니 {sum(bal.values()):,.0f}㎡ · 실면적 {gfa + sum(bal.values()):,.0f}㎡ · "
          f"건축면적(추정) {proj.area + over.area:,.0f}㎡ · 건폐율 {bcr:.2f}%")
    print(f"타워 삼각형 {n_tower:,}개 · GLB {'저장' if glb else '생략(trimesh 없음)'}")

    summary = {"L_plate": L, "W_plate": W_PLATE, "gfa": gfa,
               "balcony": sum(bal.values()), "bcr": bcr, "tri_tower": n_tower,
               "origin": [fr.cx, fr.cy], "az": fr.az, "top_m": float(V[:, 2].max())}

    if a.buildings:
        ctx = Mesh()
        n_ctx = build_context(a, fr, ctx)
        full = Mesh()
        for src in (model, ctx):
            off = len(full.verts)
            full.verts += src.verts
            for mat, fs in src.faces.items():
                full.faces.setdefault(mat, []).extend(
                    [(x + off, y + off, z + off) for x, y, z in fs])
                full.groups.setdefault(mat, []).extend(
                    [g + full._g for g in src.groups[mat]])
            full._g += src._g + 1
        write_obj(full, a.outdir / "화랑A_상세+주변.obj", fr,
                  "화랑 A안 상세 모형 + 주변 건물 화이트 모델")
        print(f"주변 건물 {n_ctx}개 포함 모형 저장")
        summary["n_context"] = n_ctx
    (a.outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False,
                                                      indent=1), encoding="utf-8")
    print(f"→ {a.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
