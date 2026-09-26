#!/usr/bin/env python3
"""화랑아파트 재건축 1개동 – 부지 내 최적 입지(위치) 도출.

형상(직사각형 29.2×23.4m, 장변 방위 345°)과 층수(55층)를 확정한 상태에서,
법정 이격거리를 지키는 범위 안에서 타워를 이동시키며
  ① 주변 학교 동지일 일조
  ② 한강 조망 개방도
를 계산해 최적 입지를 찾는다. 주차장 입출구 조건은 고려하지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from shapely.affinity import rotate, translate
from shapely.geometry import LineString, Polygon, box, mapping
from shapely.strtree import STRtree

import hwarang_massing_study as H

RESI_FLOOR_H = H.RESI_FLOOR_H
ROOFTOP_M = H.ROOFTOP_M


# --------------------------------------------------------------------------- #
# 한강 조망
# --------------------------------------------------------------------------- #
def river_azimuths(start: float, end: float, step: float = 5.0) -> list[float]:
    """한강 조망 대상 방위 구간을 샘플링한다(시계방향, 북 통과 허용)."""
    span = (end - start) % 360.0
    count = int(span / step) + 1
    return [(start + i * step) % 360.0 for i in range(count)]


@dataclass(frozen=True)
class ViewModel:
    """조망 차폐 판정용 주변 건물 모델."""

    tree: STRtree
    prisms: list[H.Prism]
    azimuths: list[float]
    reach_m: float
    floors: int

    def openness(self, x: float, y: float) -> tuple[float, float]:
        """(전층 조망 개방률, 저층 15개층 개방률)을 돌려준다.

        방위별로 시선 광선을 쏘아 가로막는 건물의 최대 높이를 구하고,
        그 높이 이상인 층은 수평 조망이 열린 것으로 본다.
        """
        low_floors = min(15, self.floors)
        open_all = open_low = 0
        for azimuth in self.azimuths:
            dx = math.sin(math.radians(azimuth)) * self.reach_m
            dy = math.cos(math.radians(azimuth)) * self.reach_m
            ray = LineString([(x, y), (x + dx, y + dy)])
            blocked_h = 0.0
            for idx in self.tree.query(ray):
                prism = self.prisms[idx]
                if prism.footprint.intersects(ray):
                    blocked_h = max(blocked_h, prism.top_m)
            first_open = math.ceil((blocked_h / RESI_FLOOR_H) + 0.5)
            open_all += max(0, self.floors - max(0, first_open - 1))
            open_low += max(0, low_floors - max(0, min(low_floors, first_open - 1)))
        total_all = len(self.azimuths) * self.floors
        total_low = len(self.azimuths) * low_floors
        return open_all / total_all * 100.0, open_low / total_low * 100.0


def build_view_model(context: Sequence[H.Prism], azimuths: Sequence[float],
                     reach_m: float, floors: int) -> ViewModel:
    prisms = [p for p in context if p.top_m > 0]
    return ViewModel(STRtree([p.footprint for p in prisms]), prisms,
                     list(azimuths), reach_m, floors)


# --------------------------------------------------------------------------- #
# 법정 이격
# --------------------------------------------------------------------------- #
def setback_report(tower: Polygon, site: Polygon, height_m: float) -> dict[str, float]:
    """대지경계선까지의 최소 이격거리와 채광 이격 규정 충족 여부."""
    gap = site.exterior.distance(tower)
    return {
        "min_setback_m": gap,
        # 건축법 시행령 제86조 제3항 제1호 – 채광창 방향 인접대지경계선까지
        # 수평거리의 2배(조례 완화 시 4배) 이하로 높이를 제한
        "daylight_need_2x_m": height_m / 2.0,
        "daylight_need_4x_m": height_m / 4.0,
        "meets_2x": gap >= height_m / 2.0,
        "meets_4x": gap >= height_m / 4.0,
    }


# --------------------------------------------------------------------------- #
def make_tower(base_shape: Polygon, x: float, y: float) -> Polygon:
    centre = base_shape.centroid
    return translate(base_shape, x - centre.x, y - centre.y)


def grid_positions(envelope: Polygon, shape: Polygon, step: float) -> list[tuple[float, float]]:
    """이격선 안쪽에 타워가 완전히 들어가는 중심 좌표 격자."""
    minx, miny, maxx, maxy = envelope.bounds
    positions: list[tuple[float, float]] = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if envelope.contains(make_tower(shape, x, y)):
                positions.append((x, y))
            x += step
        y += step
    return positions


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="화랑아파트 1개동 부지 내 입지 최적화")
    parser.add_argument("--buildings", type=Path, required=True)
    parser.add_argument("--daegyo", type=Path,
                        default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    parser.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_place"))
    parser.add_argument("--site-area", type=float, default=9395.0)
    parser.add_argument("--far", type=float, default=400.0)
    parser.add_argument("--bcr", type=float, default=60.0)
    parser.add_argument("--floors", type=int, default=55)
    parser.add_argument("--aspect", type=float, default=1.25, help="기준층 장단비")
    parser.add_argument("--long-azimuth", type=float, default=345.0, help="장변 방위각")
    parser.add_argument("--setback", type=float, default=3.0,
                        help="법정 이격거리(대지 안의 공지, 아파트 기준 m)")
    parser.add_argument("--coarse-step", type=float, default=6.0, help="1차 탐색 격자(m)")
    parser.add_argument("--fine-step", type=float, default=2.0, help="2차 정밀 격자(m)")
    parser.add_argument("--river-from", type=float, default=340.0, help="한강 조망 시작 방위")
    parser.add_argument("--river-to", type=float, default=110.0, help="한강 조망 종료 방위")
    parser.add_argument("--view-reach", type=float, default=600.0, help="조망 판정 거리(m)")
    parser.add_argument("--date", type=str, default="12-22")
    parser.add_argument("--school-radius", type=float, default=350.0)
    parser.add_argument("--step-min", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=10)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    ctx = H.prepare_analysis(args)
    site, receptors, times = ctx["site"], ctx["receptors"], ctx["times"]
    context, existing = ctx["context"], ctx["existing_hwarang"]

    floor_area = args.site_area * args.far / 100.0 / args.floors
    short = math.sqrt(floor_area / args.aspect)
    long_side = floor_area / short
    # 장변이 +x(방위 90°)인 직사각형을 (90 - 방위)만큼 돌려 장변 방위를 맞춘다
    shape = rotate(box(-long_side / 2, -short / 2, long_side / 2, short / 2),
                   90.0 - args.long_azimuth, origin=(0.0, 0.0))
    height_m = args.floors * RESI_FLOOR_H + ROOFTOP_M
    envelope = site.buffer(-args.setback)

    masks = H.build_context_masks(context, receptors, times)
    now = H.summarize(H.evaluate(receptors, existing, times, masks, args.step_min))
    now_school = H.per_school(H.evaluate(receptors, existing, times, masks, args.step_min))
    vacant = H.summarize(H.evaluate(receptors, [], times, masks, args.step_min))

    azimuths = river_azimuths(args.river_from, args.river_to)
    view = build_view_model(context, azimuths, args.view_reach, args.floors)

    print(f"타워: {long_side:.1f} × {short:.1f} m (장단비 {args.aspect:g}:1, "
          f"장변 방위 {args.long_azimuth:g}°) · {args.floors}층 H{height_m:.1f} m")
    print(f"이동 범위: 대지경계선에서 {args.setback:.1f} m 이격 "
          f"(이동가능 면적 {envelope.area:,.0f}㎡)")
    print(f"한강 조망 방위 {args.river_from:g}°~{args.river_to:g}° "
          f"({len(azimuths)}방향, 판정거리 {args.view_reach:.0f} m)")
    print(f"기준선: 현황 10층 3개동 {now['pass_pct']:.1f}% / 부지 공지 {vacant['pass_pct']:.1f}%\n")

    # 1단계: 이격선 안쪽 전역을 전 층 수광점으로 훑는다.
    # (저층 수광점만 쓰면 상층 영향이 빠져 최적 지점이 어긋나므로 전 층을 쓴다)
    positions = grid_positions(envelope, shape, args.coarse_step)
    print(f"1단계 전역탐색: 후보 위치 {len(positions)}개 "
          f"({args.coarse_step:g} m 격자, 전 층 수광점 {len(receptors)}점)", flush=True)
    scored = []
    for x, y in positions:
        tower = make_tower(shape, x, y)
        stats = H.summarize(H.evaluate(receptors, [H.Prism(tower, height_m, "T")],
                                       times, masks, args.step_min))
        scored.append((stats["pass_pct"], x, y))
    scored.sort(reverse=True)
    print(f"   1차 최적 {scored[0][0]:.1f}% @ E{scored[0][1]:,.1f} N{scored[0][2]:,.1f}\n",
          flush=True)

    # 2단계: 상위 지점 주변을 정밀 격자로 재탐색
    seeds = [(x, y) for _p, x, y in scored[:5]]
    refined: set[tuple[float, float]] = set()
    for sx, sy in seeds:
        for i in range(-2, 3):
            for j in range(-2, 3):
                x, y = sx + i * args.fine_step, sy + j * args.fine_step
                if envelope.contains(make_tower(shape, x, y)):
                    refined.add((round(x, 2), round(y, 2)))
    print(f"2단계 정밀평가: {len(refined)}개 위치", flush=True)

    rows: list[dict[str, Any]] = []
    for x, y in sorted(refined):
        tower = make_tower(shape, x, y)
        results = H.evaluate(receptors, [H.Prism(tower, height_m, "T")],
                             times, masks, args.step_min)
        stats = H.summarize(results)
        schools = H.per_school(results)
        open_all, open_low = view.openness(x, y)
        rows.append({
            "x": x, "y": y, "tower": tower, "stats": stats, "schools": schools,
            "delta_all": stats["pass_pct"] - now["pass_pct"],
            "delta_min": min(schools[j] - now_school.get(j, 0.0) for j in schools),
            "view_all": open_all, "view_low": open_low,
            "setback": setback_report(tower, site, height_m),
        })

    # ① 일조(0.2%p 밴드 – 그 이하는 모델 오차) ② 한강 조망 ③ 일조 정밀값
    # ① 전체 일조(0.5%p 밴드 – 그 이하는 수광점 표본 오차) ② 학교별 최악 저하폭
    # ③ 한강 조망 저층 개방률 ④ 일조 정밀값
    rows.sort(key=lambda r: (-round(r["stats"]["pass_pct"] * 2) / 2,
                             -round(r["delta_min"] * 2) / 2,
                             -round(r["view_low"], 1),
                             -r["stats"]["pass_pct"]))

    print(f"\n{'순위':>3}{'중심 E':>12}{'중심 N':>12}{'기준A':>8}{'현황대비':>9}"
          f"{'학교최악':>9}{'조망전층':>9}{'조망저층':>9}{'이격':>7}")
    print("-" * 82)
    for rank, row in enumerate(rows[:args.top_n], 1):
        print(f"{rank:>3}{row['x']:>12,.1f}{row['y']:>12,.1f}"
              f"{row['stats']['pass_pct']:>7.1f}%{row['delta_all']:>+8.1f}%p"
              f"{row['delta_min']:>+8.1f}%p"
              f"{row['view_all']:>8.1f}%{row['view_low']:>8.1f}%"
              f"{row['setback']['min_setback_m']:>6.1f}m")

    best = rows[0]
    print(f"\n학교별 충족률 (권장 입지)")
    for jibun in sorted(best["schools"]):
        print(f"   {jibun}: {best['schools'][jibun]:5.1f}% "
              f"(현황 {now_school[jibun]:5.1f}%, {best['schools'][jibun] - now_school[jibun]:+5.1f}%p)")

    sb = best["setback"]
    print(f"\n법정 이격 검토 (H = {height_m:.1f} m)")
    print(f"   대지경계선 최소 이격: {sb['min_setback_m']:.1f} m "
          f"(대지 안의 공지 기준 {args.setback:.1f} m 이상 → 충족)")
    print(f"   채광창 방향 이격(높이의 1/2, 2배 규정): 필요 {sb['daylight_need_2x_m']:.1f} m → "
          f"{'충족' if sb['meets_2x'] else '미충족'}")
    print(f"   조례 완화(4배 규정) 적용 시: 필요 {sb['daylight_need_4x_m']:.1f} m → "
          f"{'충족' if sb['meets_4x'] else '미충족'}")

    write_outputs(args, site, envelope, rows, now, vacant, now_school,
                  long_side, short, height_m)
    export_placement_svg(
        args.outdir, site, envelope, rows[0]["tower"], ctx["schools"], context,
        f"화랑아파트 권장 입지 – {long_side:.1f}×{short:.1f}m 장변 {args.long_azimuth:g}° "
        f"{args.floors}층 · 기준A {rows[0]['stats']['pass_pct']:.1f}% · 이격 "
        f"{rows[0]['setback']['min_setback_m']:.1f}m (상단이 북)")
    return 0



def export_placement_svg(outdir: Path, site: Polygon, envelope: Polygon,
                         tower: Polygon, schools: Sequence[dict[str, Any]],
                         context: Sequence[H.Prism], label: str) -> Path:
    """대지·이격선·권장 타워·학교를 한 장의 SVG 배치도로 그린다(상단이 북)."""
    from shapely.ops import unary_union
    layers = [site, envelope, tower, *(s["geom"] for s in schools)]
    minx, miny, maxx, maxy = unary_union(layers).buffer(25.0).bounds
    width, height = maxx - minx, maxy - miny
    px_w = 900.0
    k = px_w / width

    def path(geom) -> str:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return "".join(
            '<polygon points="%s" />' % " ".join(
                f"{(x - minx) * k:.1f},{(maxy - y) * k:.1f}"
                for x, y in poly.exterior.coords)
            for poly in polys)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{height * k + 40:.0f}" viewBox="0 0 {px_w:.0f} {height * k + 40:.0f}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>',
           f'<g transform="translate(0,40)">']
    for prism in context:
        if prism.footprint.distance(site) < 220:
            out.append(f'<g fill="#d5d5d0" fill-opacity="0.55" stroke="none">'
                       f'{path(prism.footprint)}</g>')
    for school in schools:
        out.append(f'<g fill="#2a6f97" fill-opacity="0.8" stroke="#14425c" '
                   f'stroke-width="1">{path(school["geom"])}</g>')
    out.append(f'<g fill="none" stroke="#e63946" stroke-width="2.5" '
               f'stroke-dasharray="10 6">{path(site)}</g>')
    out.append(f'<g fill="none" stroke="#f4a261" stroke-width="1.8" '
               f'stroke-dasharray="4 4">{path(envelope)}</g>')
    out.append(f'<g fill="#1d3557" fill-opacity="0.92" stroke="#0b1c30" '
               f'stroke-width="2">{path(tower)}</g>')
    out.append("</g>")
    out.append(f'<text x="12" y="24" font-family="sans-serif" font-size="16" '
               f'font-weight="bold" fill="#111">{label}</text>')
    legend = [("#1d3557", "권장 타워"), ("#e63946", "대지경계선"),
              ("#f4a261", "법정 이격선"), ("#2a6f97", "주변 학교")]
    x = px_w - 430
    for colour, text in legend:
        out.append(f'<rect x="{x}" y="12" width="13" height="12" fill="{colour}"/>'
                   f'<text x="{x + 18}" y="23" font-family="sans-serif" font-size="12.5" '
                   f'fill="#333">{text}</text>')
        x += 108
    out.append("</svg>")
    path_out = outdir / "hwarang_placement_preview.svg"
    path_out.write_text("\n".join(out), encoding="utf-8")
    return path_out


def write_outputs(args, site, envelope, rows, now, vacant, now_school,
                  long_side, short, height_m) -> None:
    def dump(path: Path, features):
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
            "features": [{"type": "Feature", "geometry": mapping(g), "properties": p}
                         for g, p in features],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    dump(args.outdir / "hwarang_setback_envelope.geojson",
         [(envelope, {"name": f"법정 이격선({args.setback:g} m) 내 이동가능 범위",
                      "area_m2": round(envelope.area, 1)})])

    top = rows[:args.top_n]
    dump(args.outdir / "hwarang_placement_top.geojson", [
        (row["tower"], {
            "rank": rank, "x": row["x"], "y": row["y"],
            "floors": args.floors, "height_m": round(height_m, 2),
            "base_elev_m": H.GROUND_ELEV_M,
            "top_elev_m": round(H.GROUND_ELEV_M + height_m, 2),
            "long_m": round(long_side, 1), "short_m": round(short, 1),
            "long_azimuth": args.long_azimuth,
            "pass_a_pct": round(row["stats"]["pass_pct"], 1),
            "delta_vs_now_pp": round(row["delta_all"], 1),
            "view_all_pct": round(row["view_all"], 1),
            "view_low_pct": round(row["view_low"], 1),
            "min_setback_m": round(row["setback"]["min_setback_m"], 1),
        })
        for rank, row in enumerate(top, 1)
    ])

    schools = sorted(rows[0]["schools"])
    with (args.outdir / "placement_comparison.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["순위", "중심E", "중심N", "기준A충족%", "기준B충족%",
                         "현황대비%p", "학교최악%p", "조망전층%", "조망저층%",
                         "최소이격m", "채광2배충족", "채광4배충족",
                         "불충족점수", "불충족평균확보h"]
                        + [f"학교{j}%" for j in schools])
        for rank, row in enumerate(rows, 1):
            st, sb = row["stats"], row["setback"]
            writer.writerow([
                rank, round(row["x"], 2), round(row["y"], 2),
                round(st["pass_pct"], 1), round(st["pass_strict_pct"], 1),
                round(row["delta_all"], 1), round(row["delta_min"], 1),
                round(row["view_all"], 1), round(row["view_low"], 1),
                round(sb["min_setback_m"], 1),
                "O" if sb["meets_2x"] else "X", "O" if sb["meets_4x"] else "X",
                st["n_fail"], round(st["fail_mean_total_h"], 2),
            ] + [round(row["schools"][j], 1) for j in schools])
    print(f"결과 저장: {args.outdir}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
