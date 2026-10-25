#!/usr/bin/env python3
"""화랑아파트 재건축 – 발코니(서비스면적) 반영 최종 형상·배치도 산출.

입지를 확정한 상태에서 발코니 1.5 m 를 외부에 반영하고,
연면적(용적률)은 내부 골조 기준으로 유지하면서 형상을 다시 최적화한다.

세 개의 선이 서로 다르다.
  · 연면적선 : 내부 골조 외벽선. 용적률 산정 면적.
  · 건축면적선 : 발코니 끝에서 1 m 후퇴한 선(건축법 시행령 제119조 제1항 제2호).
  · 외형선 : 발코니 끝선. 실제 그림자를 만드는 윤곽.

산출 우선순위
  ① 일조권이 최대한 유리한 형상
  ② 직사각형 2:1
  ③ 직사각형 1.5:1
  ④ 그 외
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
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

import hwarang_massing_study as H
import hwarang_tower_shape_study as S

RESI_FLOOR_H = H.RESI_FLOOR_H
ROOFTOP_M = H.ROOFTOP_M
BALCONY_EXEMPT_M = 1.0   # 건축면적 산입 시 발코니 끝에서 후퇴하는 거리


@dataclass
class Massing:
    """발코니를 반영한 기준층 3중 폴리곤."""

    code: str
    family: str
    label: str
    aspect: float
    balcony_mode: str
    inner: Polygon        # 연면적선
    build: Polygon        # 건축면적선
    outer: Polygon        # 외형선(그림자)
    floors: int

    @property
    def top_m(self) -> float:
        return self.floors * RESI_FLOOR_H + ROOFTOP_M

    @property
    def prism(self) -> H.Prism:
        return H.Prism(self.outer, self.top_m, self.code)

    @property
    def gfa_m2(self) -> float:
        return self.inner.area * self.floors

    @property
    def service_m2(self) -> float:
        """서비스면적(발코니) 합계 – 연면적 미산입."""
        return (self.outer.area - self.inner.area) * self.floors

    @property
    def ew_width(self) -> float:
        minx, _miny, maxx, _maxy = self.outer.bounds
        return maxx - minx


def rect_dims(area: float, aspect: float) -> tuple[float, float]:
    short = math.sqrt(area / aspect)
    return area / short, short


def make_rect_massing(
    area: float, aspect: float, rotation: float, depth: float, mode: str,
    centre: tuple[float, float], floors: int, code: str,
) -> Massing:
    """직사각형은 치수를 직접 키워 정확한 발코니 외형을 만든다."""
    long_side, short_side = rect_dims(area, aspect)
    if mode == "all":                      # 4면 발코니
        grow_l, grow_s = depth, depth
    else:                                  # 장변 2면 발코니
        grow_l, grow_s = 0.0, depth
    back = depth - BALCONY_EXEMPT_M        # 건축면적 산입분

    def rect(dl: float, ds: float) -> Polygon:
        return rotate(box(-(long_side + 2 * dl) / 2, -(short_side + 2 * ds) / 2,
                          (long_side + 2 * dl) / 2, (short_side + 2 * ds) / 2),
                      rotation, origin=(0.0, 0.0))

    inner, build, outer = rect(0.0, 0.0), rect(
        max(0.0, back) if grow_l else 0.0, max(0.0, back)), rect(grow_l, grow_s)
    cx, cy = centre
    move = lambda g: translate(g, cx, cy)  # noqa: E731
    label = (f"직사각형 {aspect:g}:1 / 회전 {rotation:g}° / 발코니 "
             f"{'4면' if mode == 'all' else '장변2면'}")
    return Massing(code, "직사각형", label, aspect, mode,
                   move(inner), move(build), move(outer), floors)


def make_free_massing(
    base: Polygon, area: float, rotation: float, depth: float,
    centre: tuple[float, float], floors: int, code: str,
    family: str, name: str,
) -> Massing:
    """직사각형 외 형상은 오프셋(버퍼)으로 발코니를 두른다."""
    unit = rotate(S.normalize(base, area), rotation, origin="centroid")
    inner = unit
    outer = unit.buffer(depth, join_style=2)
    build = unit.buffer(max(0.0, depth - BALCONY_EXEMPT_M), join_style=2)
    cx, cy = centre
    move = lambda g: translate(g, cx - unit.centroid.x, cy - unit.centroid.y)  # noqa: E731
    rect = outer.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    sides = [math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(4)]
    aspect = max(sides) / min(sides) if min(sides) else 99.0
    return Massing(code, family, f"{name} / 회전 {rotation:g}° / 발코니 4면",
                   aspect, "all", move(inner), move(build), move(outer), floors)


def build_candidates(area: float, depth: float, centre: tuple[float, float],
                     floors: int) -> list[Massing]:
    rotations = tuple(float(a) for a in range(0, 180, 15))
    out: list[Massing] = []
    index = 0
    for aspect in (2.0, 1.5, 1.25, 1.0, 2.5, 3.0):
        for rotation in ((0.0,) if aspect == 1.0 else rotations):
            for mode in ("all", "long"):
                index += 1
                out.append(make_rect_massing(area, aspect, rotation, depth, mode,
                                             centre, floors, f"R{index:03d}"))
    extras = [
        ("팔각형", "모서리컷 팔각형 2:1", S.shape_octagon(0.25, 2.0)),
        ("팔각형", "모서리컷 팔각형 1.5:1", S.shape_octagon(0.25, 1.5)),
        ("타원", "타원 2:1", S.shape_ellipse(2.0)),
        ("V(<)형", "'<' 벌림 140°", S.shape_v(140.0)),
    ]
    for family, name, base in extras:
        for rotation in rotations:
            index += 1
            out.append(make_free_massing(base, area, rotation, depth, centre,
                                         floors, f"X{index:03d}", family, name))
    return out


def aspect_rank(aspect: float) -> tuple[int, float]:
    """우선순위 ②2:1 ③1.5:1 ④그 외."""
    if abs(aspect - 2.0) <= 0.2:
        return 0, abs(aspect - 2.0)
    if abs(aspect - 1.5) <= 0.2:
        return 1, abs(aspect - 1.5)
    return 2, min(abs(aspect - 2.0), abs(aspect - 1.5))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="발코니 반영 최종 형상·배치도 산출")
    parser.add_argument("--buildings", type=Path, required=True)
    parser.add_argument("--daegyo", type=Path,
                        default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    parser.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_final"))
    parser.add_argument("--site-area", type=float, default=9395.0)
    parser.add_argument("--far", type=float, default=400.0)
    parser.add_argument("--bcr", type=float, default=60.0)
    parser.add_argument("--floors", type=int, default=55)
    parser.add_argument("--balcony", type=float, default=1.5, help="발코니 깊이(m)")
    parser.add_argument("--centre-x", type=float, default=194319.2, help="타워 중심 E")
    parser.add_argument("--centre-y", type=float, default=546962.7, help="타워 중심 N")
    parser.add_argument("--setback", type=float, default=3.0)
    parser.add_argument("--date", type=str, default="12-22")
    parser.add_argument("--school-radius", type=float, default=350.0)
    parser.add_argument("--step-min", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--floor-scan", type=str, default="49,55,60,65,70",
                        help="권장 형상에 대해 층수 민감도를 볼 층수 목록")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    ctx = H.prepare_analysis(args)
    site, receptors, times = ctx["site"], ctx["receptors"], ctx["times"]
    context, existing = ctx["context"], ctx["existing_hwarang"]
    envelope = site.buffer(-args.setback)
    centre = (args.centre_x, args.centre_y)

    gfa_target = args.site_area * args.far / 100.0
    inner_area = gfa_target / args.floors
    max_build = args.site_area * args.bcr / 100.0

    masks = H.build_context_masks(context, receptors, times)
    now = H.summarize(H.evaluate(receptors, existing, times, masks, args.step_min))
    now_school = H.per_school(H.evaluate(receptors, existing, times, masks, args.step_min))
    vacant = H.summarize(H.evaluate(receptors, [], times, masks, args.step_min))

    print(f"입지 고정: E{centre[0]:,.1f} N{centre[1]:,.1f} · {args.floors}층")
    print(f"용적률 {args.far:.0f}% → 지상연면적(내부 골조) {gfa_target:,.0f}㎡ "
          f"→ 기준층 내부 {inner_area:,.0f}㎡")
    print(f"발코니 {args.balcony:g} m (연면적 제외, 건축면적은 끝에서 "
          f"{BALCONY_EXEMPT_M:g} m 후퇴 산입)")
    print(f"기준선: 현황 {now['pass_pct']:.1f}% / 부지 공지 {vacant['pass_pct']:.1f}%\n")

    candidates = build_candidates(inner_area, args.balcony, centre, args.floors)
    print(f"형상 후보 {len(candidates)}개 평가 중...", flush=True)

    rows: list[dict[str, Any]] = []
    for cand in candidates:
        results = H.evaluate(receptors, [cand.prism], times, masks, args.step_min)
        stats = H.summarize(results)
        schools = H.per_school(results)
        rows.append({
            "cand": cand, "stats": stats, "schools": schools,
            "delta_all": stats["pass_pct"] - now["pass_pct"],
            "delta_min": min(schools[j] - now_school.get(j, 0.0) for j in schools),
            "fits": envelope.contains(cand.outer),
            "clearance": site.exterior.distance(cand.outer),
        })

    # ① 일조(0.5%p 밴드) ② 대지 수용 ③ 2:1 → 1.5:1 → 그 외 ④ 일조 정밀값
    rows.sort(key=lambda r: (-round(r["stats"]["pass_pct"] * 2) / 2,
                             not r["fits"],
                             aspect_rank(r["cand"].aspect),
                             -r["stats"]["pass_pct"]))

    print(f"\n{'순위':>3} {'형상':<44}{'기준A':>7}{'현황대비':>9}{'건폐율':>8}"
          f"{'EW폭':>7}{'이격':>7}")
    print("-" * 92)
    for rank, row in enumerate(rows[:args.top_n], 1):
        c, st = row["cand"], row["stats"]
        print(f"{rank:>3} {c.label[:43]:<44}{st['pass_pct']:>6.1f}%"
              f"{row['delta_all']:>+8.1f}%p{c.build.area / args.site_area * 100:>7.1f}%"
              f"{c.ew_width:>6.1f}m{row['clearance']:>6.1f}m")

    print(f"\n계열·장단비별 최고 성적")
    print(f"{'구분':<26}{'최고 기준A':>10}{'현황대비':>9}{'최적 회전':>10}{'발코니':>8}")
    print("-" * 66)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        c = row["cand"]
        key = (f"직사각형 {c.aspect:g}:1" if c.family == "직사각형" else c.family)
        groups.setdefault(key, []).append(row)
    for key, group in groups.items():
        best = max(group, key=lambda r: r["stats"]["pass_pct"])
        label = best["cand"].label
        rot = label.split("회전 ")[1].split("°")[0] if "회전 " in label else "-"
        print(f"{key:<26}{best['stats']['pass_pct']:>9.1f}%{best['delta_all']:>+8.1f}%p"
              f"{rot:>9}°{'4면' if best['cand'].balcony_mode == 'all' else '장변2면':>8}")

    best = rows[0]
    print(f"\n학교별 충족률 (권장 형상)")
    for jibun in sorted(best["schools"]):
        print(f"   {jibun}: {best['schools'][jibun]:5.1f}% "
              f"(현황 {now_school[jibun]:5.1f}%, "
              f"{best['schools'][jibun] - now_school[jibun]:+5.1f}%p)")

    scan = floor_sensitivity(args, best["cand"], gfa_target, centre, receptors,
                             times, masks, now, envelope, site)
    write_outputs(args, site, envelope, rows, now, vacant, ctx["schools"],
                  context, inner_area, max_build, scan)
    return 0


def floor_sensitivity(args, best: Massing, gfa_target: float, centre,
                      receptors, times, masks, now, envelope, site) -> list[dict[str, Any]]:
    """권장 형상을 유지한 채 층수를 바꿔 일조 회복 가능성을 본다."""
    print(f"\n층수 민감도 (형상 {best.aspect:g}:1, 발코니 "
          f"{'4면' if best.balcony_mode == 'all' else '장변2면'} 고정)")
    print(f"{'층수':>5}{'내부기준층':>11}{'외형':>16}{'기준A':>8}{'현황대비':>9}{'이격':>7}")
    print("-" * 58)
    rotation = float(best.label.split("회전 ")[1].split("°")[0])
    out = []
    for floors in [int(v) for v in args.floor_scan.split(",")]:
        area = gfa_target / floors
        cand = make_rect_massing(area, best.aspect, rotation, args.balcony,
                                 best.balcony_mode, centre, floors, f"F{floors}")
        stats = H.summarize(H.evaluate(receptors, [cand.prism], times, masks,
                                       args.step_min))
        ol, os_ = rect_dims(cand.outer.area, cand.aspect)
        out.append({"floors": floors, "cand": cand, "stats": stats,
                    "delta": stats["pass_pct"] - now["pass_pct"],
                    "clearance": site.exterior.distance(cand.outer),
                    "fits": envelope.contains(cand.outer)})
        print(f"{floors:>5}{area:>10,.0f}㎡{ol:>9.1f}×{os_:.1f}m"
              f"{stats['pass_pct']:>7.1f}%{out[-1]['delta']:>+8.1f}%p"
              f"{out[-1]['clearance']:>6.1f}m")
    return out


def write_outputs(args, site, envelope, rows, now, vacant, schools, context,
                  inner_area, max_build, scan) -> None:
    def dump(path: Path, features):
        path.write_text(json.dumps({
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
            "features": [{"type": "Feature", "geometry": mapping(g), "properties": p}
                         for g, p in features],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    best = rows[0]["cand"]
    dump(args.outdir / "hwarang_final_massing.geojson", [
        (best.inner, {"line": "연면적선(내부 골조)", "area_m2": round(best.inner.area, 1),
                      "floors": best.floors, "gfa_m2": round(best.gfa_m2, 1),
                      "far_pct": round(best.gfa_m2 / args.site_area * 100, 1),
                      "height_m": round(best.top_m, 2),
                      "top_elev_m": round(H.GROUND_ELEV_M + best.top_m, 2)}),
        (best.build, {"line": "건축면적선(발코니 끝 -1m)", "area_m2": round(best.build.area, 1),
                      "bcr_pct": round(best.build.area / args.site_area * 100, 2)}),
        (best.outer, {"line": "외형선(발코니 끝)", "area_m2": round(best.outer.area, 1),
                      "service_m2": round(best.service_m2, 1)}),
    ])
    dump(args.outdir / "hwarang_final_top.geojson", [
        (r["cand"].outer, {
            "rank": rank, "code": r["cand"].code, "label": r["cand"].label,
            "aspect": round(r["cand"].aspect, 2),
            "balcony_mode": r["cand"].balcony_mode,
            "inner_m2": round(r["cand"].inner.area, 1),
            "build_m2": round(r["cand"].build.area, 1),
            "outer_m2": round(r["cand"].outer.area, 1),
            "pass_a_pct": round(r["stats"]["pass_pct"], 1),
            "delta_vs_now_pp": round(r["delta_all"], 1),
            "clearance_m": round(r["clearance"], 1),
        })
        for rank, r in enumerate(rows[:args.top_n], 1)
    ])

    school_keys = sorted(rows[0]["schools"])
    with (args.outdir / "final_shape_comparison.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["순위", "코드", "계열", "형상", "장단비", "발코니",
                         "내부기준층㎡", "건축면적㎡", "외형㎡", "서비스면적㎡",
                         "연면적㎡", "용적률%", "건폐율%", "동서폭m", "이격m", "대지수용",
                         "기준A충족%", "기준B충족%", "현황대비%p", "학교최악%p"]
                        + [f"학교{j}%" for j in school_keys])
        for rank, row in enumerate(rows, 1):
            c, st = row["cand"], row["stats"]
            writer.writerow([
                rank, c.code, c.family, c.label, round(c.aspect, 2),
                "4면" if c.balcony_mode == "all" else "장변2면",
                round(c.inner.area, 1), round(c.build.area, 1), round(c.outer.area, 1),
                round(c.service_m2, 1), round(c.gfa_m2, 1),
                round(c.gfa_m2 / args.site_area * 100, 1),
                round(c.build.area / args.site_area * 100, 2),
                round(c.ew_width, 1), round(row["clearance"], 1),
                "가능" if row["fits"] else "초과",
                round(st["pass_pct"], 1), round(st["pass_strict_pct"], 1),
                round(row["delta_all"], 1), round(row["delta_min"], 1),
            ] + [round(row["schools"][j], 1) for j in school_keys])

    export_site_plan(args, site, envelope, best, schools, context)
    print(f"\n권장 형상: [{best.code}] {best.label}")
    print(f"  내부 {best.inner.area:,.0f}㎡ / 건축면적 {best.build.area:,.0f}㎡ "
          f"/ 외형 {best.outer.area:,.0f}㎡ / 서비스면적 {best.service_m2:,.0f}㎡")
    print(f"  기준A {rows[0]['stats']['pass_pct']:.1f}% "
          f"(현황 {now['pass_pct']:.1f}%, 공지 {vacant['pass_pct']:.1f}%)")
    print(f"결과 저장: {args.outdir}")


def export_site_plan(args, site, envelope, best: Massing, schools, context) -> Path:
    """대지·이격선·3중 외곽선·학교를 담은 최종 배치도(SVG)."""
    layers = [site, envelope, best.outer, *(s["geom"] for s in schools)]
    minx, miny, maxx, maxy = unary_union(layers).buffer(22.0).bounds
    width, height = maxx - minx, maxy - miny
    px_w = 980.0
    k = px_w / width

    def path(geom) -> str:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return "".join(
            '<polygon points="%s" />' % " ".join(
                f"{(x - minx) * k:.1f},{(maxy - y) * k:.1f}" for x, y in p.exterior.coords)
            for p in polys)

    long_o, short_o = rect_dims(best.outer.area, best.aspect)
    long_i, short_i = rect_dims(best.inner.area, best.aspect)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{height * k + 84:.0f}" viewBox="0 0 {px_w:.0f} {height * k + 84:.0f}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>',
           '<g transform="translate(0,60)">']
    for prism in context:
        if prism.footprint.distance(site) < 200:
            out.append(f'<g fill="#d8d8d3" fill-opacity="0.5">{path(prism.footprint)}</g>')
    for school in schools:
        out.append(f'<g fill="#2a6f97" fill-opacity="0.8" stroke="#14425c" '
                   f'stroke-width="1">{path(school["geom"])}</g>')
    out.append(f'<g fill="none" stroke="#e63946" stroke-width="2.5" '
               f'stroke-dasharray="10 6">{path(site)}</g>')
    out.append(f'<g fill="none" stroke="#f4a261" stroke-width="1.6" '
               f'stroke-dasharray="4 4">{path(envelope)}</g>')
    out.append(f'<g fill="#8ecae6" fill-opacity="0.55" stroke="#457b9d" '
               f'stroke-width="1.4">{path(best.outer)}</g>')
    out.append(f'<g fill="none" stroke="#2a9d8f" stroke-width="1.6" '
               f'stroke-dasharray="6 3">{path(best.build)}</g>')
    out.append(f'<g fill="#1d3557" fill-opacity="0.9" stroke="#0b1c30" '
               f'stroke-width="1.8">{path(best.inner)}</g>')
    out.append("</g>")
    out.append(f'<text x="12" y="22" font-family="sans-serif" font-size="16" '
               f'font-weight="bold" fill="#111">화랑아파트 최종 배치도 – '
               f'{best.label} · {best.floors}층 H{best.top_m:.1f} m (상단이 북)</text>')
    out.append(f'<text x="12" y="42" font-family="sans-serif" font-size="12.5" fill="#444">'
               f'내부 {long_i:.1f}×{short_i:.1f} m ({best.inner.area:,.0f}㎡) · '
               f'외형 {long_o:.1f}×{short_o:.1f} m ({best.outer.area:,.0f}㎡) · '
               f'건축면적 {best.build.area:,.0f}㎡ (건폐율 '
               f'{best.build.area / args.site_area * 100:.1f}%) · '
               f'서비스면적 {best.service_m2:,.0f}㎡</text>')
    legend = [("#1d3557", "연면적선"), ("#2a9d8f", "건축면적선"), ("#8ecae6", "외형선(발코니)"),
              ("#e63946", "대지경계"), ("#f4a261", "이격선"), ("#2a6f97", "학교")]
    x = 12.0
    for colour, text in legend:
        out.append(f'<rect x="{x}" y="{50}" width="12" height="10" fill="{colour}"/>'
                   f'<text x="{x + 16}" y="{59}" font-family="sans-serif" '
                   f'font-size="11.5" fill="#333">{text}</text>')
        x += 88 + 8 * len(text)
    out.append("</svg>")
    target = args.outdir / "hwarang_final_site_plan.svg"
    target.write_text("\n".join(out), encoding="utf-8")
    return target


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
