#!/usr/bin/env python3
"""학교 지번의 AL_D010 폴리곤 식별용 배치도.

사용자가 준 정면도·분석지점도가 GIS의 어느 동인지 맞춰야 수광점을 넣을 수 있다.
이 스크립트는 지번 하나의 모든 건물 폴리곤에 기호(A, B, C…)를 붙이고
면적·층수·실측높이와 함께 **방위별 외벽 길이**를 같이 찍어, 도면의 치수와
바로 대조할 수 있게 한다.
"""

from __future__ import annotations

import argparse
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Point
from shapely.ops import unary_union

import hwarang_massing_study as H
from daegyo_school_sunlight import (
    load_named_buildings, school_label, school_parcel_rows,
)

PALETTE = ["#f4a261", "#2a9d8f", "#e9c46a", "#8ecae6", "#e76f51",
           "#bdb2ff", "#ffb4a2", "#a8dadc", "#c8b6a6", "#adb5bd"]
# 방위 구간 – 여의도 일대는 블록이 약 52° 돌아가 있어 SE/SW/NE/NW 로 본다
SECTORS = [("남동", 112.0, 172.0), ("남서", 202.0, 262.0),
           ("북서", 292.0, 352.0), ("북동", 22.0, 82.0)]


def wall_azimuth(poly, x0, y0, x1, y1) -> float | None:
    ex, ey = x1 - x0, y1 - y0
    n = math.hypot(ex, ey)
    if n < 0.2:
        return None
    nx, ny = ey / n, -ex / n
    if poly.contains(Point((x0 + x1) / 2 + nx * 0.3, (y0 + y1) / 2 + ny * 0.3)):
        nx, ny = -nx, -ny
    return math.degrees(math.atan2(nx, ny)) % 360


def sector_lengths(geom) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
        coords = list(poly.exterior.coords)
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            az = wall_azimuth(poly, x0, y0, x1, y1)
            if az is None:
                continue
            for name, lo, hi in SECTORS:
                if (az - lo) % 360 <= (hi - lo) % 360:
                    out[name] += math.hypot(x1 - x0, y1 - y0)
                    break
    return out


def axis_span(geom, axis_az: float, origin: float) -> tuple[float, float]:
    ang = math.radians(axis_az)
    ux, uy = math.sin(ang), math.cos(ang)
    vals = [x * ux + y * uy
            for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom])
            for x, y in poly.exterior.coords]
    return min(vals) - origin, max(vals) - origin


def key_svg(path: Path, rows: Sequence[dict[str, Any]], title: str,
            axis_az: float) -> tuple[int, int]:
    geoms = [r["geom"] for r in rows]
    minx, miny, maxx, maxy = unary_union(geoms).buffer(14).bounds
    head, foot, pad = 118.0, 40.0, 26.0
    scale = min((1320.0 - 2 * pad) / (maxx - minx),
                (880.0 - head - foot) / (maxy - miny))
    px_w = (maxx - minx) * scale + 2 * pad
    px_h = (maxy - miny) * scale + head + foot

    def P(x, y):
        return (x - minx) * scale + pad, (maxy - y) * scale + head

    ang = math.radians(axis_az)
    origin = min(x * math.sin(ang) + y * math.cos(ang)
                 for r in rows
                 for poly in (r["geom"].geoms
                              if r["geom"].geom_type == "MultiPolygon"
                              else [r["geom"]])
                 for x, y in poly.exterior.coords)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h:.0f}" viewBox="0 0 {px_w:.0f} {px_h:.0f}">',
           '<rect width="100%" height="100%" fill="#fcfcfa"/>',
           f'<text x="18" y="34" font-family="sans-serif" font-size="20" '
           f'font-weight="bold" fill="#111">{title} – 건물 폴리곤 식별도</text>',
           f'<text x="18" y="58" font-family="sans-serif" font-size="12.5" '
           f'fill="#555">각 동의 면적 / AL_D010 층수·실측높이 / 방위별 외벽 길이. '
           f'상단이 북.</text>',
           f'<text x="18" y="78" font-family="sans-serif" font-size="12.5" '
           f'fill="#555">s = 방위 {axis_az:.0f}° 축(블록 장축) 위 위치(m). '
           f'정면도 전개 길이와 대조하는 데 씁니다.</text>',
           f'<text x="18" y="98" font-family="sans-serif" font-size="12.5" '
           f'fill="#b23a48">층수·높이가 0 인 동은 AL_D010 에 값이 없는 것입니다'
           f'(도면으로 복원해야 함).</text>']

    for i, row in enumerate(sorted(rows, key=lambda r: -r["geom"].area)):
        g = row["geom"]
        tag = chr(ord("A") + i)
        for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            pts = " ".join(f"{P(x,y)[0]:.1f},{P(x,y)[1]:.1f}"
                           for x, y in poly.exterior.coords)
            svg.append(f'<polygon points="{pts}" fill="{PALETTE[i % 10]}" '
                       f'fill-opacity="0.55" stroke="#333" stroke-width="1.3"/>')
        sec = sector_lengths(g)
        lo, hi = axis_span(g, axis_az, origin)
        c = g.centroid
        X, Y = P(c.x, c.y)
        lines = [f'{tag}  {g.area:,.0f}㎡',
                 f'{row["floors"]}층 · {row.get("height", 0.0):.2f}m',
                 f's={lo:.0f}~{hi:.0f}m',
                 "남동 %.0f / 남서 %.0f" % (sec.get("남동", 0), sec.get("남서", 0))]
        for k, text in enumerate(lines):
            svg.append(f'<text x="{X:.1f}" y="{Y - 18 + k*14:.1f}" '
                       f'font-family="sans-serif" font-size="{13 if k==0 else 11}" '
                       f'font-weight="{"bold" if k==0 else "normal"}" '
                       f'text-anchor="middle" fill="#111">{text}</text>')
    svg.append(f'<g transform="translate({px_w-58:.0f},{head+6:.0f})">'
               '<line x1="0" y1="40" x2="0" y2="6" stroke="#111" stroke-width="2"/>'
               '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
               '<text x="0" y="56" font-family="sans-serif" font-size="12" '
               'text-anchor="middle" fill="#111">N</text></g>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")
    return int(px_w), int(px_h)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="학교 건물 폴리곤 식별도")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--jibun", type=str, required=True)
    p.add_argument("--axis-az", type=float, default=52.0)
    p.add_argument("--outdir", type=Path, default=Path("outputs/school_receptors"))
    p.add_argument("--school-radius", type=float, default=350.0)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    buildings = load_named_buildings(args.buildings)
    daegyo = H.load_daegyo(args.daegyo)
    centre = unary_union([p.footprint for p in daegyo]).centroid
    parcel = school_parcel_rows(buildings, centre, args.school_radius)
    if args.jibun not in parcel:
        raise SystemExit(f"{args.jibun} 은 학교 지번으로 잡히지 않았습니다.")
    # 높이가 없어 school_parcel_rows 가 버린 동도 식별도에는 보여준다
    rows = [{**b, "floors": b["floors"]} for b in buildings
            if b.get("jibun") == args.jibun and b["geom"].area >= 5.0]
    name = school_label(args.jibun, parcel[args.jibun])
    out = args.outdir / f"{args.jibun}_key.svg"
    w, h = key_svg(out, rows, name, args.axis_az)
    print(f"■ {name} – 폴리곤 {len(rows)}개 → {out} ({w}x{h})")
    ang = math.radians(args.axis_az)
    origin = min(x * math.sin(ang) + y * math.cos(ang)
                 for r in rows
                 for poly in (r["geom"].geoms
                              if r["geom"].geom_type == "MultiPolygon"
                              else [r["geom"]])
                 for x, y in poly.exterior.coords)
    for i, row in enumerate(sorted(rows, key=lambda r: -r["geom"].area)):
        sec = sector_lengths(row["geom"])
        lo, hi = axis_span(row["geom"], args.axis_az, origin)
        print(f"   {chr(ord('A')+i)} {row['geom'].area:8.1f}㎡ "
              f"{row['floors']}층 {row.get('height',0.0):6.2f}m "
              f"s={lo:6.1f}~{hi:6.1f} "
              f"남동 {sec.get('남동',0):5.1f}m 남서 {sec.get('남서',0):5.1f}m "
              f"북서 {sec.get('북서',0):5.1f}m 북동 {sec.get('북동',0):5.1f}m "
              f"{row.get('name','')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
