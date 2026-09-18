#!/usr/bin/env python3
"""운동장 분석지점 격자 확인도 – 도면대로 놓였는지 눈으로 대조한다.

`data/school_playgrounds.json` 에 정의한 학교의 운동장 경계·격자·제외 칸을
주변 건물과 함께 그린다. 칸마다 (i,j) 를 찍으므로 원본 분석지점도와 한 칸씩
맞춰 볼 수 있다.

    python3 school_playground_check.py --buildings <AL_D010.gpkg> --jibun 40-2
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Sequence

from shapely.geometry import Polygon
from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
import school_playground as PG
from daegyo_school_sunlight import load_named_buildings, school_parcel_rows
from school_receptor_compliance import build_points

FILL = {"30-1": "#ffc9c9", "40-1": "#ffe6a8", "40-2": "#ffc7e0", "40-3": "#bfe3ff"}


def cell_grid(rect: Polygon, ni: int, nj: int):
    """(i, j) → 칸 폴리곤. school_playground.grid_points 와 같은 방향 규칙."""
    e = list(rect.exterior.coords)[:4]
    a, b, c, _d = e                       # a,b,c,d 는 폴리곤 입력 순서
    u = ((b[0] - c[0]) / ni, (b[1] - c[1]) / ni)
    v = ((a[0] - b[0]) / nj, (a[1] - b[1]) / nj)

    def poly(i: int, j: int) -> Polygon:
        p = (c[0] + u[0] * (i - 1) + v[0] * (j - 1),
             c[1] + u[1] * (i - 1) + v[1] * (j - 1))
        return Polygon([p, (p[0] + u[0], p[1] + u[1]),
                        (p[0] + u[0] + v[0], p[1] + u[1] + v[1]),
                        (p[0] + v[0], p[1] + v[1])])
    return poly


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--jibun", required=True, help="예: 40-2")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--width", type=int, default=1560)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    spec_all = PG.load_spec()
    if args.jibun not in spec_all:
        raise SystemExit(f"{args.jibun} 은 school_playgrounds.json 에 없다")
    spec = spec_all[args.jibun]
    rect = Polygon(spec["polygon"])
    cells = PG.cells(rect, spec)
    ni, nj = spec["grid"]
    skip = {tuple(e) for e in spec.get("exclude", ())}
    poly_of = cell_grid(rect, ni, nj)

    buildings = load_named_buildings(args.buildings)
    daegyo = H.load_daegyo(args.daegyo)
    centre = unary_union([q.footprint for q in daegyo]).centroid
    schools = school_parcel_rows(buildings, centre, 600.0)
    all_b = unary_union([r["geom"] for r in buildings])
    pts, _rec, _g = build_points(schools, buildings, SF.load_spec(), all_b,
                                 60.0, {}, 8.0)
    face = [p for p in pts if p.jibun == args.jibun and p.kind != "운동장 지반"]

    near_box = rect.buffer(55)
    near = [r for r in buildings if r["geom"].intersects(near_box)]
    minx, miny, maxx, maxy = near_box.bounds
    W = args.width
    Hh = int(W * (maxy - miny) / (maxx - minx))
    sc = min(W / (maxx - minx), Hh / (maxy - miny)) * 0.94
    ox, oy = minx, maxy

    def tx(x, y):
        return (x - ox) * sc + 40, (oy - y) * sc + 100

    def path(geom):
        out = []
        for pg in (geom.geoms if geom.geom_type.startswith("Multi") else [geom]):
            d = []
            for i, (x, y) in enumerate(pg.exterior.coords):
                px, py = tx(x, y)
                d.append(f"{'M' if i == 0 else 'L'}{px:.1f},{py:.1f}")
            out.append(" ".join(d) + " Z")
        return " ".join(out)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W+80}" '
         f'height="{Hh+150}" viewBox="0 0 {W+80} {Hh+150}">'
         f'<rect width="100%" height="100%" fill="#fff"/>']
    for r in near:
        s.append(f'<path d="{path(r["geom"])}" '
                 f'fill="{FILL.get(r.get("jibun") or "", "#eee")}" '
                 f'stroke="#888" stroke-width="1"/>')
    for i in range(1, ni + 1):
        for j in range(1, nj + 1):
            pg = poly_of(i, j)
            miss = (i, j) in skip
            dash = ' stroke-dasharray="6,4"' if miss else ''
            s.append(f'<path d="{path(pg)}" '
                     f'fill="{"#f0f0f0" if miss else "#fff3d0"}" '
                     f'fill-opacity="0.85" '
                     f'stroke="{"#bbbbbb" if miss else "#e07b00"}" '
                     f'stroke-width="{1.0 if miss else 1.4}"{dash}/>')
            cx, cy = tx(pg.centroid.x, pg.centroid.y)
            s.append(f'<text x="{cx:.1f}" y="{cy+11:.1f}" font-size="13" '
                     f'text-anchor="middle" fill="{"#aaa" if miss else "#555"}">'
                     f'({i},{j})</text>')
    s.append(f'<path d="{path(rect)}" fill="none" stroke="#d00000" '
             f'stroke-width="3.5"/>')
    for p in face:
        px, py = tx(p.x, p.y)
        s.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.6" fill="#0a9396"/>')
    for _i, _j, x, y in cells:
        px, py = tx(x, y)
        s.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5.5" fill="#d00000"/>')

    e = list(rect.exterior.coords)
    sides = [math.dist(e[k], e[k + 1]) for k in range(4)]
    miss_txt = ("" if not skip else
                " · 회색 점선 = 도면에 없는 칸 "
                + "·".join(f"({i},{j})" for i, j in sorted(skip)))
    s.append(f'<text x="40" y="44" font-size="28" font-weight="bold">'
             f'{spec["school"]}({args.jibun}) 운동장 – 분석지점도 '
             f'{ni} × {nj} 재현</text>')
    s.append(f'<text x="40" y="76" font-size="20">'
             f'빨강 점 {len(cells)}개 = 분석지점{miss_txt} · '
             f'경계 {rect.area:,.0f}㎡ ({sides[0]:.1f}×{sides[1]:.1f}m, '
             f'칸 {sides[1]/ni:.2f}×{sides[0]/nj:.2f}m) · '
             f'청록 점 = 교사동 창면 수광점</text>')
    x0, y0 = tx(minx + 12, miny + 14)
    s.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x0+50*sc:.1f}" '
             f'y2="{y0:.1f}" stroke="#000" stroke-width="3"/>'
             f'<text x="{x0:.1f}" y="{y0-9:.1f}" font-size="20">50 m</text>')
    s.append("</svg>")

    out = args.out or (Path("outputs/school_compliance/playground")
                       / f"{spec['school']}_운동장격자_확인도.svg")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(s), encoding="utf-8")
    print(f"{spec['school']} {ni}x{nj} · 분석지점 {len(cells)}개 · "
          f"{rect.area:,.0f}㎡ · 칸 {sides[1]/ni:.2f}x{sides[0]/nj:.2f}m")
    print(f"   → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
