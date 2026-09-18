#!/usr/bin/env python3
"""화랑 설계안 배치도 – `hwarang_design_2026.py` 가 낸 GeoJSON 으로 그린다.

탐색을 다시 돌리지 않는다. 산출된 매싱·수광점 GeoJSON 만 읽어 SVG 를 그리고,
크로미움이 있으면 PNG 까지 낸다.

    python3 hwarang_plan_2026.py --buildings <AL_D010.gpkg>
    python3 hwarang_plan_2026.py --buildings <...> --stem 갈래_타워+저층별동
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import shape
from shapely.ops import unary_union

CHROME = ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/opt/pw-browsers/chromium/chrome-linux/chrome")
CTX_RADIUS = 260.0          # 배경으로 그릴 주변 건물 반경(m)


def load_context(gpkg: Path, centre, radius: float) -> list[Any]:
    """AL_D010 에서 주변 건물 외곽선만 가볍게 읽는다."""
    from shapely import wkb as _wkb

    def read_geom(blob: bytes):
        flags = blob[3]
        env = [0, 32, 48, 48, 64][(flags >> 1) & 0x07]
        return _wkb.loads(blob[8 + env:])

    conn = sqlite3.connect(gpkg)
    table = conn.execute("select table_name from gpkg_contents "
                         "where data_type='features'").fetchone()[0]
    out = []
    for (blob,) in conn.execute(f'select geom from "{table}"'):
        if blob is None:
            continue
        g = read_geom(blob)
        if g.is_empty or g.area < 1.0:
            continue
        if g.centroid.distance(centre) <= radius:
            out.append(g)
    conn.close()
    return out


def plan_svg(path: Path, masses, site, pts, context, title: str, sub: str,
             ) -> tuple[int, int]:
    geoms = [g for g, _ in masses] + [site] + list(context)
    minx, miny, maxx, maxy = unary_union(geoms).buffer(12).bounds
    head, foot, pad = 112.0, 30.0, 20.0
    scale = min((1440.0 - 2 * pad) / (maxx - minx),
                (1000.0 - head - foot) / (maxy - miny))
    W = (maxx - minx) * scale + 2 * pad
    Hh = (maxy - miny) * scale + head + foot

    def P(x, y):
        return (x - minx) * scale + pad, (maxy - y) * scale + head

    def ring(poly):
        return " ".join(f"{P(x, y)[0]:.1f},{P(x, y)[1]:.1f}"
                        for x, y in poly.exterior.coords)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
         f'height="{Hh:.0f}" viewBox="0 0 {W:.0f} {Hh:.0f}">',
         '<rect width="100%" height="100%" fill="#fcfcfa"/>',
         f'<text x="18" y="32" font-family="sans-serif" font-size="19" '
         f'font-weight="bold" fill="#111">{title}</text>',
         f'<text x="18" y="56" font-family="sans-serif" font-size="12.5" '
         f'fill="#555">{sub}</text>',
         '<text x="18" y="76" font-family="sans-serif" font-size="12.5" '
         'fill="#555">붉은 점 = 신규 불충족(충족→불충족) · 파란 점 = 신규 충족 · '
         '회색 점 = 그 밖의 수광점</text>',
         '<text x="18" y="94" font-family="sans-serif" font-size="12.5" '
         'fill="#555">진한 남색 = 화랑 타워(외형선 = 발코니 끝) · '
         '연한 하늘 = 저층부 · 붉은 파선 = 화랑 대지경계. 상단이 북.</text>']
    for g in context:
        for q in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            s.append(f'<polygon points="{ring(q)}" fill="#e6e4de" '
                     f'stroke="#b6b4ae" stroke-width="0.6"/>')
    s.append(f'<polygon points="{ring(site)}" fill="none" stroke="#c1121f" '
             f'stroke-width="1.7" stroke-dasharray="7 5"/>')
    for g, props in masses:
        tall = "타워" in str(props.get("kind", ""))
        fill = "#1d3557" if tall else "#8ecae6"
        s.append(f'<polygon points="{ring(g)}" fill="{fill}" '
                 f'fill-opacity="0.88" stroke="#0b1d33" stroke-width="1.2"/>')
        c = g.centroid
        x, y = P(c.x, c.y)
        s.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" '
                 f'font-size="11.5" font-weight="bold" text-anchor="middle" '
                 f'fill="{"#fff" if tall else "#0b1d33"}">'
                 f'{props.get("height_m", 0):.0f}m</text>')

    rank = {"new_fail": 2, "new_ok": 1}
    for f in sorted(pts, key=lambda q: rank.get(q["properties"]["change_cd"], 0)):
        p = f["properties"]
        x, y = P(*f["geometry"]["coordinates"])
        cd = p["change_cd"]
        if cd == "new_fail":
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" '
                     f'fill="#d62828" stroke="#fff" stroke-width="0.8"/>')
        elif cd == "new_ok":
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.0" '
                     f'fill="#1d65b5" stroke="#fff" stroke-width="0.7"/>')
        else:
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.3" '
                     f'fill="#b9b9b3"/>')
    s.append(f'<g transform="translate({W-52:.0f},{head+2:.0f})">'
             '<line x1="0" y1="40" x2="0" y2="6" stroke="#111" stroke-width="2"/>'
             '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
             '<text x="0" y="56" font-family="sans-serif" font-size="12" '
             'text-anchor="middle" fill="#111">N</text></g>')
    bar = 50.0 * scale
    s.append(f'<g transform="translate({pad+4:.0f},{Hh-14:.0f})">'
             f'<line x1="0" y1="0" x2="{bar:.1f}" y2="0" stroke="#111" '
             'stroke-width="2"/>'
             f'<text x="{bar/2:.1f}" y="-5" font-family="sans-serif" '
             'font-size="11" text-anchor="middle" fill="#111">50 m</text></g>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")
    return int(W), int(Hh)


def to_png(svg: Path, png: Path, w: int, h: int) -> bool:
    exe = next((c for c in CHROME if Path(c).exists()), None) or shutil.which("chromium")
    if not exe:
        return False
    url = "file://" + urllib.parse.quote(str(svg.resolve()))
    subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu",
                    f"--screenshot={png}", f"--window-size={w},{h}", url],
                   capture_output=True, timeout=120)
    return png.exists()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, default=None,
                   help="AL_D010 GeoPackage. 주면 주변 건물을 배경으로 깐다")
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_2026"))
    p.add_argument("--stem", type=str, default="최적안")
    p.add_argument("--title", type=str, default=None)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    mass_path = args.outdir / f"{args.stem}_매싱_epsg5186.geojson"
    pts_path = args.outdir / f"{args.stem}_수광점_전체_epsg5186.geojson"
    if not mass_path.exists():
        raise SystemExit(f"매싱 파일이 없다: {mass_path}")
    masses, site = [], None
    for f in json.loads(mass_path.read_text(encoding="utf-8"))["features"]:
        g, props = shape(f["geometry"]), f["properties"]
        if props.get("kind") == "대지":
            site = g
        else:
            masses.append((g, props))
    if site is None:
        raise SystemExit("대지 폴리곤을 찾지 못했다")
    pts = (json.loads(pts_path.read_text(encoding="utf-8"))["features"]
           if pts_path.exists() else [])

    context = []
    if args.buildings and args.buildings.exists():
        context = [g for g in load_context(args.buildings, site.centroid,
                                           CTX_RADIUS)
                   if not g.intersects(site.buffer(-2))]

    n_fail = sum(1 for f in pts if f["properties"]["change_cd"] == "new_fail")
    n_ok = sum(1 for f in pts if f["properties"]["change_cd"] == "new_ok")
    top = max((p.get("height_m", 0) for _g, p in masses), default=0)
    title = args.title or f"화랑 재건축 {args.stem} – 최고 {top:.1f}m"
    sub = (f"신규 불충족 {n_fail}개 · 신규 충족 {n_ok}개 · "
           f"수광점 {len(pts):,}개 (동지 08~16시, 기준A)")
    svg = args.outdir / f"{args.stem}_배치도.svg"
    w, h = plan_svg(svg, masses, site, pts, context, title, sub)
    png = args.outdir / f"{args.stem}_배치도.png"
    ok = to_png(svg, png, w, h)
    print(f"■ {args.stem} 배치도 → {svg}" + (f" · {png}" if ok else " (PNG 실패)"))
    print(f"   매스 {len(masses)}개 · 최고 {top:.1f}m · 배경 건물 {len(context)}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
