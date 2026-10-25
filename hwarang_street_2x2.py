#!/usr/bin/env python3
"""화랑 – 20층 4개동 · 남서–북동 보행로를 사이에 둔 2x2 배치.

요청(2026-09-20): 앞선 2x2 안의 통로는 **남동↔북서**로 짧고 넓게 뚫려
있었다. 그 대신 **남서→북동으로 길고 좁은 길**을 내고, 그 양옆에 건물을
세운다. 대교아파트 쪽(서, 최단 5.9m)에서 여의도초등학교 쪽(북동 50.9°,
최단 17.6m)으로 걸어가는 길이다.

대지 장축이 방위 52°(122.7m)이고 단축이 142°(76.5m)이므로, 길을 장축과
나란히 놓으면 길이 자연히 길어지고(대지를 가로질러 116m 남짓) 폭은
단축 쪽에서 떼어 쓰게 되어 좁아진다. 이전 안(단축과 나란한 통로)은 그
반대라 폭 62m·길이 76m의 '마당'에 가까웠다.

    길 방향      = 대지 장축(52°) — 남서에서 북동
    짝 배치      = 길과 나란히 2개동씩 한 줄(길 양옆에 한 줄씩)
    타워 장변    = 길과 나란 — 길 쪽으로 **장변(가로벽)** 이 서게 한다
    짝 안 간격   = 작게(기본 4~10m) — 한 줄의 두 동은 붙인다
    길 폭        = 두 줄 사이 순간격

**일조권 분석은 요청대로 생략한다.** 기하·면적만 계산한다.

    python3 hwarang_street_2x2.py
    python3 hwarang_street_2x2.py --path-widths 12 15 18 --min-depth 11
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any, Sequence

from shapely import affinity
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from hwarang_redesign import BALCONY_M, COVERAGE_INSET_M, plate_polygon

RESI_FLOOR_H = 3.3
ROOFTOP_M = 4.0
SITE_GEOJSON = Path("outputs/hwarang_20f_2x2_2026/최적안_매싱_epsg5186.geojson")
CHROME = ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/opt/pw-browsers/chromium/chrome-linux/chrome")
CTX_RADIUS = 260.0


# --------------------------------------------------------------------------- #
# 대지·배경
# --------------------------------------------------------------------------- #
def load_site(path: Path) -> Polygon:
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        if f["properties"].get("kind") == "대지":
            return shape(f["geometry"])
    raise SystemExit(f"{path} 에 대지 폴리곤이 없습니다.")


def long_axis_az(site: Polygon) -> float:
    """대지 최소외접사각형의 **긴 변** 방위(0~180)."""
    pts = list(site.minimum_rotated_rectangle.exterior.coords)[:4]
    best, best_len = 0.0, -1.0
    for i in range(4):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 4]
        d = math.hypot(x1 - x0, y1 - y0)
        if d > best_len:
            best_len = d
            best = math.degrees(math.atan2(x1 - x0, y1 - y0)) % 180
    return best


def load_context(gpkg: Path, centre, radius: float) -> list[Any]:
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


# --------------------------------------------------------------------------- #
# 배치
# --------------------------------------------------------------------------- #
def layout(plate: float, aspect: float, path_az: float, pair_gap: float,
           path_w: float, centre: tuple[float, float]
           ) -> tuple[list[Polygon], list[Polygon], list[tuple[float, float]]]:
    """길 양옆 한 줄씩, 한 줄에 2개동. (외형선, 건축면적선, 중심) 반환.

    길 방향 u = path_az, 가로질러 v = path_az+90.
      u 방향: 한 줄의 두 동이 pair_gap 을 두고 선다
      v 방향: 두 줄이 path_w 를 두고 마주본다(장변끼리)
    """
    short = math.sqrt(plate / aspect)
    lng = plate / short
    du = (lng + 2 * BALCONY_M) / 2 + pair_gap / 2      # 줄 안 중심 간 반간격
    dv = (short + 2 * BALCONY_M) / 2 + path_w / 2      # 줄 사이 중심 간 반간격
    ux, uy = math.sin(math.radians(path_az)), math.cos(math.radians(path_az))
    vx, vy = math.cos(math.radians(path_az)), -math.sin(math.radians(path_az))
    outs, covs, cens = [], [], []
    for sv in (-1, 1):
        for su in (-1, 1):
            cx = centre[0] + ux * su * du + vx * sv * dv
            cy = centre[1] + uy * su * du + vy * sv * dv
            cens.append((cx, cy))
            outs.append(plate_polygon(plate, aspect, path_az, cx, cy,
                                      BALCONY_M))
            covs.append(plate_polygon(plate, aspect, path_az, cx, cy,
                                      BALCONY_M - COVERAGE_INSET_M))
    return outs, covs, cens


def path_polygon(site: Polygon, path_az: float, path_w: float,
                 centre: tuple[float, float]) -> Polygon:
    """길 폴리곤 — 두 줄 사이의 띠를 대지 경계로 자른 것."""
    band = affinity.rotate(
        Polygon([(centre[0] - 400, centre[1] - path_w / 2),
                 (centre[0] + 400, centre[1] - path_w / 2),
                 (centre[0] + 400, centre[1] + path_w / 2),
                 (centre[0] - 400, centre[1] + path_w / 2)]),
        90.0 - path_az, origin=(centre[0], centre[1]))
    return band.intersection(site)


def extent_along(geom, az: float) -> float:
    ux, uy = math.sin(math.radians(az)), math.cos(math.radians(az))
    ts = [x * ux + y * uy for x, y in geom.exterior.coords]
    return max(ts) - min(ts)


def option(site: Polygon, envelope: Polygon, plate: float, aspect: float,
           path_az: float, pair_gap: float, path_w: float,
           centre: tuple[float, float], floors: int, site_area: float,
           gfa: float, min_gap: float) -> dict[str, Any] | None:
    outs, covs, _c = layout(plate, aspect, path_az, pair_gap, path_w, centre)
    if not all(envelope.contains(p) for p in outs):
        return None
    for i in range(4):
        for j in range(i + 1, 4):
            if outs[i].distance(outs[j]) < min_gap - 1e-6:
                return None
    short = math.sqrt(plate / aspect)
    lng = plate / short
    road = path_polygon(site, path_az, path_w, centre)
    if road.is_empty or road.geom_type != "Polygon":
        return None
    cover = unary_union(covs).area
    serv = sum(p.area for p in outs) * floors - gfa
    return {
        "aspect": aspect, "pair_gap": pair_gap, "path_w": path_w,
        "centre": centre, "path_az": path_az,
        "short_m": short, "long_m": lng,
        # 가로벽 = 실제로 건물이 선 길이(줄 안 틈은 뺀다). 틈까지 더한 값을
        # 쓰면 "틈을 벌릴수록 가로벽이 길다"가 되어 '붙이고' 지시와 어긋난다.
        "frontage_m": 2 * (lng + 2 * BALCONY_M),
        "span_m": 2 * (lng + 2 * BALCONY_M) + pair_gap,
        "path_len_m": extent_along(road, path_az),
        "path_area_m2": road.area,
        "cover_m2": cover, "bcr_pct": cover / site_area * 100,
        "service_m2": serv, "real_m2": gfa + serv,
        "outs": outs, "road": road,
    }


def search(site: Polygon, envelope: Polygon, args, plate: float, gfa: float,
           path_az: float) -> list[dict[str, Any]]:
    cx0, cy0 = site.centroid.x, site.centroid.y
    ux, uy = math.sin(math.radians(path_az)), math.cos(math.radians(path_az))
    vx, vy = math.cos(math.radians(path_az)), -math.sin(math.radians(path_az))
    out = []
    for daz in args.az_offsets:
        az = (path_az + daz) % 180
        for aspect in args.aspects:
            if math.sqrt(plate / aspect) < args.min_short:
                continue
            for pair_gap in args.pair_gaps:
                for path_w in args.path_widths:
                    for su in args.offsets:
                        for sv in args.offsets:
                            c = (cx0 + ux * su + vx * sv,
                                 cy0 + uy * su + vy * sv)
                            r = option(site, envelope, plate, aspect, az,
                                       pair_gap, path_w, c, args.floors,
                                       args.site_area, gfa, args.min_dong_gap)
                            if r:
                                out.append(r)
    return out


# --------------------------------------------------------------------------- #
# 그림
# --------------------------------------------------------------------------- #
def draw(path: Path, r: dict[str, Any], site: Polygon, context, floors: int,
         title: str, sub: str) -> tuple[int, int]:
    geoms = list(r["outs"]) + [site, r["road"]] + list(context)
    minx, miny, maxx, maxy = unary_union(geoms).buffer(26).bounds
    head, foot, pad = 116.0, 30.0, 20.0
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
         'fill="#555">진한 남색 = 20층 주동(외형선 = 발코니 끝) · '
         '연두 띠 = 남서–북동 보행로 · 붉은 파선 = 대지경계. 상단이 북.</text>',
         '<text x="18" y="96" font-family="sans-serif" font-size="12.5" '
         'fill="#555">서쪽 대교아파트(최단 5.9m) → 북동 여의도초(최단 17.6m) '
         '로 이어지는 길. 일조 분석은 생략.</text>']
    for g in context:
        for q in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            s.append(f'<polygon points="{ring(q)}" fill="#e6e4de" '
                     f'stroke="#b6b4ae" stroke-width="0.6"/>')
    for q in (r["road"].geoms if r["road"].geom_type == "MultiPolygon"
              else [r["road"]]):
        s.append(f'<polygon points="{ring(q)}" fill="#a8d5a2" '
                 f'fill-opacity="0.55" stroke="#4f8a52" stroke-width="1.2" '
                 f'stroke-dasharray="6 4"/>')
    s.append(f'<polygon points="{ring(site)}" fill="none" stroke="#c1121f" '
             f'stroke-width="1.7" stroke-dasharray="7 5"/>')
    for g in r["outs"]:
        s.append(f'<polygon points="{ring(g)}" fill="#1d3557" '
                 f'fill-opacity="0.9" stroke="#0b1d33" stroke-width="1.2"/>')
        c = g.centroid
        x, y = P(c.x, c.y)
        s.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" '
                 f'font-size="11.5" font-weight="bold" text-anchor="middle" '
                 f'fill="#fff">{floors}층</text>')
    # 길 라벨은 길의 **양 끝 바깥**에 단다 — 가운데에 달면 주동 라벨을 덮는다.
    ux = math.sin(math.radians(r["path_az"]))
    uy = math.cos(math.radians(r["path_az"]))
    rc = r["road"].centroid
    half = r["path_len_m"] / 2 + 13.0
    for sgn, label in ((-1, "← 대교아파트 쪽"), (1, "여의도초 쪽 →")):
        x, y = P(rc.x + ux * sgn * half, rc.y + uy * sgn * half)
        s.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="sans-serif" '
                 f'font-size="13" font-weight="bold" text-anchor="middle" '
                 f'fill="#20531f">{label}</text>')
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
    exe = next((c for c in CHROME if Path(c).exists()), None) \
        or shutil.which("chromium")
    if not exe:
        return False
    url = "file://" + urllib.parse.quote(str(svg.resolve()))
    subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu",
                    f"--screenshot={png}", f"--window-size={w},{h}", url],
                   capture_output=True, timeout=120)
    return png.exists()


def write_geojson(path: Path, r: dict[str, Any], site: Polygon,
                  floors: int) -> None:
    top = floors * RESI_FLOOR_H + ROOFTOP_M
    feats = [{"type": "Feature", "geometry": g.__geo_interface__,
              "properties": {"kind": f"타워{i+1} 외형선", "height_m": round(top, 2)}}
             for i, g in enumerate(r["outs"])]
    feats.append({"type": "Feature", "geometry": r["road"].__geo_interface__,
                  "properties": {"kind": "보행로",
                                 "width_m": round(r["path_w"], 1),
                                 "length_m": round(r["path_len_m"], 1),
                                 "area_m2": round(r["path_area_m2"])}})
    feats.append({"type": "Feature", "geometry": site.__geo_interface__,
                  "properties": {"kind": "대지", "area_m2": round(site.area)}})
    path.write_text(json.dumps(
        {"type": "FeatureCollection",
         "crs": {"type": "name",
                 "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
         "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", type=Path, default=SITE_GEOJSON)
    p.add_argument("--buildings", type=Path, default=None,
                   help="AL_D010 gpkg. 주면 배치도에 주변 건물을 깐다")
    p.add_argument("--outdir", type=Path,
                   default=Path("outputs/hwarang_20f_street_2026"))
    p.add_argument("--site-area", type=float, default=9395.0)
    p.add_argument("--far", type=float, default=400.0)
    p.add_argument("--setback", type=float, default=3.0)
    p.add_argument("--floors", type=int, default=20)
    p.add_argument("--floor-h", type=float, default=3.3)
    p.add_argument("--min-short", type=float, default=8.5)
    p.add_argument("--min-dong-gap", type=float, default=4.0)
    p.add_argument("--aspects", type=float, nargs="*",
                   default=[2.5, 3.0, 3.5, 3.88, 4.5, 5.0, 5.5, 6.0, 6.5])
    p.add_argument("--pair-gaps", type=float, nargs="*",
                   default=[4.0, 6.0, 8.0, 10.0])
    p.add_argument("--path-widths", type=float, nargs="*",
                   default=[10.0, 12.0, 15.0, 18.0, 20.0, 25.0, 30.0])
    p.add_argument("--offsets", type=float, nargs="*",
                   default=[-6.0, -3.0, 0.0, 3.0, 6.0])
    p.add_argument("--az-offsets", type=float, nargs="*", default=[0.0])
    p.add_argument("--min-depth", type=float, default=11.0,
                   help="권장안의 기준층 깊이 하한(11m=편복도형 표준)")
    p.add_argument("--target-width", type=float, default=15.0,
                   help="권장안의 목표 보행로 폭 m")
    a = p.parse_args(argv)
    global RESI_FLOOR_H
    RESI_FLOOR_H = a.floor_h
    a.outdir.mkdir(parents=True, exist_ok=True)

    site = load_site(a.site)
    envelope = site.buffer(-a.setback)
    path_az = long_axis_az(site)
    gfa = a.site_area * a.far / 100.0
    plate = gfa / (4 * a.floors)
    top = a.floors * a.floor_h + ROOFTOP_M
    print(f"대지 {site.area:,.0f}㎡ · 장축 방위 {path_az:.1f}° (남서→북동) · "
          f"연면적 {gfa:,.0f}㎡ · 동당 기준층 {plate:,.1f}㎡ · 높이 {top:.1f}m")

    rows = search(site, envelope, a, plate, gfa, path_az)
    if not rows:
        raise SystemExit("배치 가능한 안이 없습니다 — 폭/세장비 범위를 넓히세요.")
    print(f"배치 가능 {len(rows)}개\n")

    # 세장비(=깊이)별 최장 가로벽
    print(f"{'세장비':>7}{'동 치수':>18}{'가로벽m':>9}{'최대 길폭m':>11}"
          f"{'깊이m':>8}{'건폐율%':>9}{'실면적㎡':>11}")
    by_aspect = {}
    for r in rows:
        k = round(r["aspect"], 2)
        if k not in by_aspect or r["frontage_m"] > by_aspect[k]["frontage_m"]:
            by_aspect[k] = r
    for k in sorted(by_aspect):
        r = by_aspect[k]
        wmax = max(x["path_w"] for x in rows if round(x["aspect"], 2) == k)
        print(f"{k:>6g}:1{r['long_m']:>10.1f}×{r['short_m']:<6.1f}"
              f"{r['frontage_m']:>9.1f}{wmax:>11.0f}{r['short_m']:>8.1f}"
              f"{r['bcr_pct']:>9.2f}{r['real_m2']:>11,.0f}")

    # 권장안 — 깊이 하한을 지키면서 가로벽이 가장 긴 안, 목표 폭에 가장 가깝게
    pool = [r for r in rows if r["short_m"] >= a.min_depth - 1e-6]
    if not pool:
        pool = rows
        print(f"\n(깊이 {a.min_depth:g}m 이상인 안이 없어 전체에서 고릅니다)")
    best_front = max(r["frontage_m"] for r in pool)
    near = [r for r in pool if r["frontage_m"] >= best_front - 0.5]
    # 같은 가로벽이면 ① 목표 폭에 가깝고 ② 줄 안 틈이 좁고('붙이고')
    # ③ 길이 긴 안을 고른다
    rec = min(near, key=lambda r: (abs(r["path_w"] - a.target_width),
                                   r["pair_gap"], -r["path_len_m"]))

    def spec(r):
        return [
            ("구성", f"20층 4개동 · 동당 기준층 {plate:,.0f}㎡ "
                    f"({r['long_m']:.1f} × {r['short_m']:.1f}m) · "
                    f"방위 {r['path_az']:.0f}°"),
            ("배치", f"길 양옆에 한 줄씩(줄마다 2개동) · "
                    f"줄 안 간격 {r['pair_gap']:.0f}m"),
            ("**보행로**", f"**폭 {r['path_w']:.0f}m · 길이 "
                         f"{r['path_len_m']:.0f}m · 면적 "
                         f"{r['path_area_m2']:,.0f}㎡**"),
            ("길 양옆 가로벽 길이", f"{r['frontage_m']:.0f}m "
                             f"(줄 안 틈 {r['pair_gap']:.0f}m 포함 "
                             f"{r['span_m']:.0f}m — 길 길이의 "
                             f"{r['span_m']/r['path_len_m']*100:.0f}%)"),
            ("높이", f"{top:,.1f}m (20층 × {a.floor_h:g}m + 옥탑 {ROOFTOP_M:g}m)"),
            ("연면적", f"{gfa:,.0f}㎡ · 용적률 {a.far:g}%"),
            ("서비스면적(발코니 1.5m)", f"{r['service_m2']:,.0f}㎡"),
            ("**실면적**", f"**{r['real_m2']:,.0f}㎡**"),
            ("건축면적 · 건폐율", f"{r['cover_m2']:,.0f}㎡ · {r['bcr_pct']:.2f}%"),
            ("300세대 기준", f"실면적 {r['real_m2']/300:.1f}㎡/세대"),
        ]

    print(f"\n■ 권장안 — 보행로 폭 {rec['path_w']:.0f}m · 길이 "
          f"{rec['path_len_m']:.0f}m · 가로벽 {rec['frontage_m']:.0f}m "
          f"(줄 안 틈 {rec['pair_gap']:.0f}m)")
    for k, v in spec(rec):
        print(f"   {k.replace('**','')}: {v.replace('**','')}")

    # 폭 트레이드오프(권장안과 같은 세장비·짝간격)
    same = [r for r in rows
            if abs(r["aspect"] - rec["aspect"]) < 1e-9
            and abs(r["pair_gap"] - rec["pair_gap"]) < 1e-9]
    widths = {}
    for r in same:
        k = round(r["path_w"])
        if k not in widths or r["path_len_m"] > widths[k]["path_len_m"]:
            widths[k] = r

    context = []
    if a.buildings:
        context = load_context(a.buildings, site.centroid, CTX_RADIUS)

    L = ["# 화랑 – 20층 4개동 · 남서–북동 보행로형 2x2 배치", "",
         "요청대로 **일조권 분석은 생략**했다. 기하와 면적만 계산한다.", "",
         "## 길의 방향", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 길 방향 | **{path_az:.0f}° — 남서 → 북동** (대지 장축과 나란) |",
         "| 잇는 곳 | 서쪽 **대교아파트** 쪽 경계(최단 5.9m) → "
         "북동쪽 **여의도초등학교**(최단 17.6m) |",
         f"| 길 길이 | 대지를 가로질러 **{rec['path_len_m']:.0f}m** |",
         f"| 길 폭 | **{rec['path_w']:.0f}m** |",
         "| 양옆 | 한 줄에 2개동씩, 장변(가로벽)이 길을 향한다 |", "",
         "종전 2x2 안의 통로는 대지 **단축**(142°)과 나란해 폭 62m·길이 76m의",
         "'마당'에 가까웠다. 이번은 **장축**(52°)과 나란해 길이가 배로 늘고",
         "폭은 좁아진다 — 걷는 길이 된다.", "",
         "## 권장안 제원", "", "| 항목 | 값 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in spec(rec)]

    L += ["", "## 길 폭별 (권장안과 같은 동 치수·줄 간격)", "",
          "| 길 폭 | 길 길이 | 길 면적 | 건폐율 | 실면적 |",
          "|---:|---:|---:|---:|---:|"]
    for k in sorted(widths):
        r = widths[k]
        mark = "**" if r is rec or abs(r["path_w"] - rec["path_w"]) < 1e-9 else ""
        L.append(f"| {mark}{r['path_w']:.0f}m{mark} | {r['path_len_m']:.0f}m | "
                 f"{r['path_area_m2']:,.0f}㎡ | {r['bcr_pct']:.2f}% | "
                 f"{r['real_m2']:,.0f}㎡ |")
    L += ["", "길 폭을 바꿔도 **건폐율·실면적은 그대로**다 — 연면적이 고정이고",
          "동 크기가 같기 때문이다. 길 폭은 순전히 두 줄을 얼마나 벌리느냐의",
          "문제이고, 그 대가는 대지 가장자리(북서·남동)에 남는 여유폭이다.", ""]

    L += ["## 동 치수(세장비)별 — 가로벽을 얼마나 길게 세울 수 있나", "",
          "| 세장비 | 동 치수 | 가로벽 길이 | 가능한 최대 길 폭 | 건폐율 | 실면적 |",
          "|---:|---|---:|---:|---:|---:|"]
    for k in sorted(by_aspect):
        r = by_aspect[k]
        wmax = max(x["path_w"] for x in rows if round(x["aspect"], 2) == k)
        mark = "**" if abs(k - round(rec["aspect"], 2)) < 1e-9 else ""
        L.append(f"| {mark}{k:g}:1{mark} | {r['long_m']:.1f} × "
                 f"{r['short_m']:.1f}m | {r['frontage_m']:.0f}m | "
                 f"{wmax:.0f}m | {r['bcr_pct']:.2f}% | {r['real_m2']:,.0f}㎡ |")
    L += ["", "동을 가늘고 길게 할수록(세장비↑) 길 양옆 가로벽이 길어져 '길'이",
          "선명해지지만, 기준층 깊이가 얕아져 평면이 불리해진다(8.5m = 편복도형",
          f"극단, 11m = 편복도형 표준, 14m = 양면복도형). 권장안은 깊이 "
          f"{a.min_depth:g}m 이상에서 가로벽이 가장 긴 안으로 골랐다.", ""]

    L += ["## 산출물", "", "| 파일 | 내용 |", "|---|---|",
          "| `권장안_배치도.png/svg` | 배치도(보행로 강조) |",
          "| `권장안_매싱_epsg5186.geojson` | 주동 외형선 + 보행로 + 대지 |", "",
          f"기하 검토 {len(rows):,}개. 일조 계산 없음(요청).", ""]
    (a.outdir / "report.md").write_text("\n".join(L), encoding="utf-8")

    write_geojson(a.outdir / "권장안_매싱_epsg5186.geojson", rec, site,
                  a.floors)
    svg = a.outdir / "권장안_배치도.svg"
    w, h = draw(svg, rec, site, context, a.floors,
                f"화랑 20층 4개동 – 남서–북동 보행로형 2x2",
                f"보행로 폭 {rec['path_w']:.0f}m · 길이 "
                f"{rec['path_len_m']:.0f}m · 가로벽 {rec['frontage_m']:.0f}m · "
                f"건폐율 {rec['bcr_pct']:.2f}% · 실면적 {rec['real_m2']:,.0f}㎡")
    png = a.outdir / "권장안_배치도.png"
    ok = to_png(svg, png, w, h)
    print(f"\n→ {a.outdir} (배치도 {'PNG+SVG' if ok else 'SVG'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
