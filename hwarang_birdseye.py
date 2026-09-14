#!/usr/bin/env python3
"""화랑 확정 설계안 – 조감도(축측투영) 이미지 생성.

지도(평면도)로는 높이 관계가 보이지 않아, 3D 각기둥을 축측투영(axonometric)
으로 그려 화랑 신축안·대교 신축안·학교 교사동의 높이 관계와 동지일 그림자를
한 장에 담는다.

카메라 방위(어느 쪽에서 바라볼지)와 앙각을 바꿔 여러 장을 만든다.
모든 형상은 실좌표(EPSG:5186) 기준이며, 화랑 신축안은 **외형선(발코니 끝)**
으로 그린다.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

import hwarang_massing_study as H
from daegyo_school_sunlight import (
    HWARANG_JIBUN, load_named_buildings, resolved_height,
    school_label, school_parcel_rows,
)
from hwarang_redesign import build_design, outline_polygon


def explode(geom):
    """MultiPolygon을 단일 Polygon 목록으로 분해한다."""
    return list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]


@dataclass
class Solid:
    """조감도에 그릴 입체."""
    footprint: Polygon
    top_m: float
    kind: str          # 'hwarang' | 'daegyo' | 'school' | 'context' | 'old'
    label: str = ""


PALETTE = {
    # (지붕, 밝은 면, 어두운 면, 외곽선)
    "hwarang": ("#2b4c7e", "#3d6ba5", "#22406b", "#12233c"),
    "daegyo":  ("#8d99ae", "#a7b1c2", "#6f7c94", "#3b4553"),
    "school":  ("#c1666b", "#d38287", "#a3545a", "#5c2a2e"),
    "context": ("#d8d8d2", "#e4e4de", "#c5c5bd", "#a8a89f"),
    "old":     ("#b9b9b2", "#c9c9c2", "#a5a59e", "#8a8a82"),
}


class Camera:
    """축측투영 카메라 – 방위(어느 쪽에서 보는가)와 앙각."""

    def __init__(self, azimuth: float, elevation: float, scale: float = 1.0):
        self.a = math.radians(azimuth)
        self.e = math.radians(elevation)
        self.scale = scale

    def depth(self, x: float, y: float) -> float:
        """카메라에서 멀어지는 방향의 거리(클수록 멀다)."""
        return -(x * math.sin(self.a) + y * math.cos(self.a))

    def project(self, x: float, y: float, z: float) -> tuple[float, float]:
        sx = x * math.cos(self.a) - y * math.sin(self.a)
        up = z * math.cos(self.e) + self.depth(x, y) * math.sin(self.e)
        return sx * self.scale, -up * self.scale


def shade(colour: str, factor: float) -> str:
    c = colour.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(v * factor))) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def ground_shadow(solids: Sequence[Solid], azimuth: float,
                  altitude: float) -> Polygon | None:
    polys = []
    for s in solids:
        if s.kind == "context":
            continue
        p = H.shadow_polygon(H.Prism(s.footprint, s.top_m, ""), 0.0,
                             azimuth, altitude)
        if p is not None and not p.is_empty:
            polys.append(p)
    return unary_union(polys) if polys else None


def render(path: Path, solids: Sequence[Solid], cam_az: float, cam_el: float,
           title: str, subtitle: str, sun: tuple[float, float] | None,
           width: float = 1400.0) -> None:
    cam = Camera(cam_az, cam_el)
    shadow = ground_shadow(solids, *sun) if sun else None

    # 화면 범위 산정
    xs, ys = [], []
    geoms = [s.footprint for s in solids] + ([shadow] if shadow else [])
    for g in geoms:
        gg = g.geoms if g.geom_type == "MultiPolygon" else [g]
        for poly in gg:
            for x, y in poly.exterior.coords:
                for z in (0.0, max(s.top_m for s in solids)):
                    px, py = cam.project(x, y, z)
                    xs.append(px)
                    ys.append(py)
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    pad = 30.0
    scale = (width - 2 * pad) / (maxx - minx)
    height = (maxy - miny) * scale + 2 * pad + 128

    def proj(x: float, y: float, z: float) -> tuple[float, float]:
        px, py = cam.project(x, y, z)
        return (px - minx) * scale + pad, (py - miny) * scale + pad + 72

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
           f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
           '<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
           '<stop offset="0%" stop-color="#eef3f8"/>'
           '<stop offset="100%" stop-color="#fbfbf7"/></linearGradient></defs>',
           f'<rect width="100%" height="100%" fill="url(#sky)"/>',
           f'<text x="18" y="32" font-family="sans-serif" font-size="20" '
           f'font-weight="bold" fill="#111">{title}</text>',
           f'<text x="18" y="56" font-family="sans-serif" font-size="13" '
           f'fill="#555">{subtitle}</text>']

    if shadow:
        gg = shadow.geoms if shadow.geom_type == "MultiPolygon" else [shadow]
        for poly in gg:
            pts = " ".join(f"{proj(x, y, 0)[0]:.1f},{proj(x, y, 0)[1]:.1f}"
                           for x, y in poly.exterior.coords)
            svg.append(f'<polygon points="{pts}" fill="#5b6472" '
                       f'fill-opacity="0.30" stroke="none"/>')

    # 지면(각 입체의 바닥 윤곽)
    for s in sorted(solids, key=lambda s: -cam.depth(s.footprint.centroid.x,
                                                     s.footprint.centroid.y)):
        pts = " ".join(f"{proj(x, y, 0)[0]:.1f},{proj(x, y, 0)[1]:.1f}"
                       for x, y in s.footprint.exterior.coords)
        svg.append(f'<polygon points="{pts}" fill="#00000010" stroke="none"/>')

    pieces: list[tuple[float, str]] = []
    for s in solids:
        roof, lit, dark, edge = PALETTE[s.kind]
        coords = list(s.footprint.exterior.coords)[:-1]
        for (x0, y0), (x1, y1) in zip(coords, coords[1:] + coords[:1]):
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            ex, ey = x1 - x0, y1 - y0
            nx, ny = ey, -ex
            n = math.hypot(nx, ny) or 1.0
            nx, ny = nx / n, ny / n
            if s.footprint.contains(Point(mx + nx * 0.3, my + ny * 0.3)):
                nx, ny = -nx, -ny
            if -(nx * math.sin(cam.a) + ny * math.cos(cam.a)) <= 0:
                continue
            quad = [proj(x0, y0, 0), proj(x1, y1, 0),
                    proj(x1, y1, s.top_m), proj(x0, y0, s.top_m)]
            bearing = math.degrees(math.atan2(nx, ny)) % 360
            f = 0.80 + 0.28 * math.cos(math.radians(bearing - 170))
            pts = " ".join(f"{p[0]:.1f},{p[1]:.1f}" for p in quad)
            pieces.append((cam.depth(mx, my),
                           f'<polygon points="{pts}" fill="{shade(lit, f)}" '
                           f'stroke="{edge}" stroke-width="0.4" '
                           f'stroke-linejoin="round"/>'))
        pts = " ".join(f"{proj(x, y, s.top_m)[0]:.1f},"
                       f"{proj(x, y, s.top_m)[1]:.1f}" for x, y in coords)
        c = s.footprint.centroid
        pieces.append((cam.depth(c.x, c.y) - 1e-6,
                       f'<polygon points="{pts}" fill="{roof}" stroke="{edge}" '
                       f'stroke-width="0.5" stroke-linejoin="round"/>'))

    for _, frag in sorted(pieces, key=lambda t: -t[0]):
        svg.append(frag)

    # 라벨(높은 것만)
    for s in solids:
        if not s.label:
            continue
        c = s.footprint.centroid
        px, py = proj(c.x, c.y, s.top_m)
        svg.append(f'<text x="{px:.1f}" y="{py - 8:.1f}" font-family="sans-serif" '
                   f'font-size="12" font-weight="bold" text-anchor="middle" '
                   f'fill="#111" stroke="#fff" stroke-width="3" '
                   f'paint-order="stroke">{s.label}</text>')

    legend = [("hwarang", "화랑 신축(본 설계안)"), ("daegyo", "대교 신축안"),
              ("school", "학교 교사동"), ("context", "기존 건물")]
    for i, (kind, text) in enumerate(legend):
        x = 18 + i * 240
        y = height - 30
        svg.append(f'<rect x="{x}" y="{y - 11}" width="15" height="15" '
                   f'fill="{PALETTE[kind][0]}" stroke="#333" stroke-width="0.6"/>')
        svg.append(f'<text x="{x + 22}" y="{y + 1}" font-family="sans-serif" '
                   f'font-size="12.5" fill="#111">{text}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="화랑 설계안 조감도")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_birdseye"))
    p.add_argument("--site-area", type=float, default=9395.0)
    p.add_argument("--far", type=float, default=400.0)
    p.add_argument("--floors", type=int, required=True)
    p.add_argument("--aspect", type=float, required=True)
    p.add_argument("--azimuth", type=float, required=True)
    p.add_argument("--x", type=float, required=True)
    p.add_argument("--y", type=float, required=True)
    p.add_argument("--name", type=str, default="설계안")
    p.add_argument("--radius", type=float, default=330.0)
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--date", type=str, default="12-22")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    from pyproj import CRS, Transformer

    buildings = load_named_buildings(args.buildings)
    daegyo = H.load_daegyo(args.daegyo)
    gfa = args.site_area * args.far / 100.0
    design = build_design(1, args.floors, args.aspect, args.azimuth,
                          [(args.x, args.y)], gfa)
    centre = design.prisms[0].footprint.centroid

    dg_centre = unary_union([p.footprint for p in daegyo]).centroid
    schools = school_parcel_rows(buildings, dg_centre, args.school_radius)
    school_labels = {j: school_label(j, rows) for j, rows in schools.items()}

    solids: list[Solid] = []
    for p in design.prisms:
        for poly in explode(p.footprint):
            solids.append(Solid(poly, p.top_m, "hwarang",
                                f"화랑 {args.floors}F"))
    for p in daegyo:
        if p.footprint.centroid.distance(centre) > args.radius:
            continue
        for poly in explode(p.footprint):
            solids.append(Solid(poly, p.top_m, "daegyo", ""))
    tallest = max((s for s in solids if s.kind == "daegyo"),
                  key=lambda s: (s.top_m, s.footprint.area), default=None)
    if tallest is not None:
        tallest.label = "대교 신축"

    school_ids = set()
    for jibun, rows in schools.items():
        for r in rows:
            if not r["classroom"]:
                continue
            if r["geom"].centroid.distance(centre) > args.radius:
                continue
            for poly in explode(r["geom"]):
                solids.append(Solid(poly, r["height_m"], "school", ""))
            school_ids.add(id(r["geom"]))
    for row in buildings:
        if row["jibun"] == HWARANG_JIBUN or id(row["geom"]) in school_ids:
            continue
        if row["geom"].centroid.distance(centre) > args.radius:
            continue
        h = resolved_height(row)
        if h <= 0:
            continue
        g = row["geom"]
        for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            if poly.area < 30:
                continue
            solids.append(Solid(poly, h, "context", ""))

    # 학교명 라벨은 가장 큰 교사동에 붙인다
    for jibun, rows in schools.items():
        cls = [r for r in rows if r["classroom"]
               and r["geom"].centroid.distance(centre) <= args.radius]
        if not cls:
            continue
        big = max(cls, key=lambda r: r["geom"].area)
        cand = [s for s in solids
                if s.kind == "school" and big["geom"].contains(s.footprint.centroid)]
        if cand:
            max(cand, key=lambda s: s.footprint.area).label = \
                school_labels[jibun].split(" ", 1)[1]

    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    month, day = (int(v) for v in args.date.split("-"))
    times = H.sun_track(month, day, lat, lon, step_min=10)
    noon = min(times, key=lambda t: abs(t[0] - 12.5))
    morning = min(times, key=lambda t: abs(t[0] - 10.0))

    lng, sht = design.plate_dims
    b = design.balcony_m
    sub = (f"1개동 {args.floors}층 · 연면적선 {design.plate_m2:,.0f}㎡"
           f"({lng:.1f}×{sht:.1f}m) · 외형선(발코니 끝) {design.outline_m2:,.0f}㎡"
           f"({lng+2*b:.1f}×{sht+2*b:.1f}m) · 높이 {design.height_m:.1f}m · "
           f"용적률 400% · 서비스면적 {design.service_m2:,.0f}㎡")

    views = [
        ("sw", 225.0, 30.0, "남서측 조감", noon),
        ("se", 135.0, 30.0, "남동측 조감", noon),
        ("s", 180.0, 22.0, "정남측 조감(학교 방향 단면감)", noon),
        ("high", 200.0, 55.0, "고각 조감(그림자 전개)", morning),
    ]
    for key, caz, cel, cname, sun in views:
        clock, alt, saz = sun
        out = args.outdir / f"hwarang_birdseye_{key}.svg"
        render(out, solids, caz, cel,
               f"{args.name} – {cname}",
               sub + f" · 그림자 {int(clock):02d}:{int(round((clock%1)*60)):02d} "
                     f"(동지일, 고도 {alt:.0f}°)",
               (saz, alt))
        print(f"저장: {out}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
