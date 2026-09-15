#!/usr/bin/env python3
"""'신규 불충족 0' 설계안 – 확정안 재평가·보고서·배치도.

hwarang_zero_newfail.py 가 찾은 best.json(또는 --fix-* 로 직접 지정한 제원)을
다시 평가해서
    · 학교·교사동/운동장별 전후 비교표
    · 신규 불충족·신규 충족 수광점 목록
    · 배치도 SVG
    · QGIS 레이어
를 만든다. 탐색을 다시 돌리지 않고 확정안만 검증할 때 쓴다.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from shapely.ops import unary_union

import hwarang_massing_study as H
from hwarang_zero_newfail import (
    Design, PODIUM_FLOORS, evaluate_design, export_design, setup,
)
from school_receptor_compliance import pass_a


def load_design(args, gfa: float) -> Design:
    if args.fix_floors:
        floors = args.fix_floors
        pod = args.fix_podium_plate or 0.0
        pf = PODIUM_FLOORS if pod > 0 else 0
        tower = (gfa - pod * pf) / (floors - pf)
        return Design(floors, args.fix_aspect, args.fix_azimuth, args.fix_x,
                      args.fix_y, tower, pf, pod, args.fix_podium_aspect,
                      args.fix_podium_x, args.fix_podium_y)
    data = json.loads((args.outdir / "best.json").read_text(encoding="utf-8"))
    pf = int(data["podium_floors"])
    return Design(int(data["floors"]), float(data["aspect"]),
                  float(data["azimuth"]), float(data["x"]), float(data["y"]),
                  float(data["tower_plate"]), pf, float(data["podium_plate"]),
                  2.2, 0.0, 0.0)


def plan_svg(path: Path, ctx: dict[str, Any], design: Design,
             flips: Sequence[int], gains: Sequence[int]) -> tuple[int, int]:
    pts = ctx["points"]
    geoms = ([p.footprint for p in ctx["context"]]
             + [poly for _n, poly in design.outlines()] + [ctx["site"]])
    minx, miny, maxx, maxy = unary_union(geoms).buffer(15).bounds
    head, foot, pad = 116.0, 34.0, 22.0
    scale = min((1420.0 - 2 * pad) / (maxx - minx),
                (980.0 - head - foot) / (maxy - miny))
    W = (maxx - minx) * scale + 2 * pad
    Hh = (maxy - miny) * scale + head + foot

    def P(x, y):
        return (x - minx) * scale + pad, (maxy - y) * scale + head

    def poly_str(poly):
        return " ".join(f"{P(x,y)[0]:.1f},{P(x,y)[1]:.1f}"
                        for x, y in poly.exterior.coords)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
         f'height="{Hh:.0f}" viewBox="0 0 {W:.0f} {Hh:.0f}">',
         '<rect width="100%" height="100%" fill="#fcfcfa"/>',
         f'<text x="18" y="34" font-family="sans-serif" font-size="20" '
         f'font-weight="bold" fill="#111">화랑 신규 불충족 0 설계안 – '
         f'{design.label()}</text>',
         f'<text x="18" y="58" font-family="sans-serif" font-size="12.5" '
         f'fill="#555">붉은 점 = 신규 불충족(충족→불충족) · 파란 점 = 신규 충족 · '
         f'회색 점 = 그 밖의 수광점. 상단이 북.</text>',
         f'<text x="18" y="78" font-family="sans-serif" font-size="12.5" '
         f'fill="#555">진한 파랑 = 화랑 타워 외형선(발코니 끝) · 연한 파랑 = 저층부 · '
         f'노랑 = 대교 신축안</text>']
    daegyo_fp = {id(p) for p in ctx["daegyo_new"]}
    for p in ctx["context"]:
        fill = "#e9c46a" if id(p) in daegyo_fp else "#e4e2dc"
        s.append(f'<polygon points="{poly_str(p.footprint)}" fill="{fill}" '
                 f'stroke="#a8a8a2" stroke-width="0.7"/>')
    for label, pg in ctx["grounds"].items():
        for q in (pg.geoms if pg.geom_type == "MultiPolygon" else [pg]):
            s.append(f'<polygon points="{poly_str(q)}" fill="#2a9d8f" '
                     f'fill-opacity="0.13" stroke="#2a9d8f" '
                     f'stroke-width="0.6"/>')
    s.append(f'<polygon points="{poly_str(ctx["site"])}" fill="none" '
             f'stroke="#c1121f" stroke-width="1.6" stroke-dasharray="7 5"/>')
    for name, poly in design.outlines():
        fill = "#1d3557" if "타워" in name else "#8ecae6"
        s.append(f'<polygon points="{poly_str(poly)}" fill="{fill}" '
                 f'fill-opacity="0.85" stroke="#0b1d33" stroke-width="1.2"/>')

    flip_set, gain_set = set(flips), set(gains)
    for i, p in enumerate(pts):
        if i in flip_set or i in gain_set:
            continue
        x, y = P(p.x, p.y)
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.3" fill="#b9b9b3"/>')
    for i in gain_set:
        x, y = P(pts[i].x, pts[i].y)
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.0" fill="#1d65b5" '
                 f'stroke="#fff" stroke-width="0.7"/>')
    for i in flip_set:
        x, y = P(pts[i].x, pts[i].y)
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="#d62828" '
                 f'stroke="#fff" stroke-width="0.8"/>')
    s.append(f'<g transform="translate({W-56:.0f},{head+4:.0f})">'
             '<line x1="0" y1="40" x2="0" y2="6" stroke="#111" stroke-width="2"/>'
             '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
             '<text x="0" y="56" font-family="sans-serif" font-size="12" '
             'text-anchor="middle" fill="#111">N</text></g>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")
    return int(W), int(Hh)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="확정안 재평가·보고서")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_zero"))
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
    p.add_argument("--playground", type=Path, default=None)
    p.add_argument("--floors-min", type=int, default=39)
    p.add_argument("--floors-max", type=int, default=55)
    p.add_argument("--pos-step", type=float, default=8.0)
    p.add_argument("--probe", action="store_true")
    p.add_argument("--fix-floors", type=int)
    p.add_argument("--fix-aspect", type=float, default=2.0)
    p.add_argument("--fix-azimuth", type=float, default=150.0)
    p.add_argument("--fix-x", type=float, default=0.0)
    p.add_argument("--fix-y", type=float, default=0.0)
    p.add_argument("--fix-podium-plate", type=float, default=0.0)
    p.add_argument("--fix-podium-aspect", type=float, default=2.2)
    p.add_argument("--fix-podium-x", type=float, default=0.0)
    p.add_argument("--fix-podium-y", type=float, default=0.0)
    p.add_argument("--report", type=Path,
                   default=Path("docs/hwarang_zero_newfail.md"))
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    ctx = setup(args)
    design = load_design(args, ctx["gfa"])
    if design.podium_floors and design.podium_cx == 0.0:
        c = ctx["envelope"].centroid
        design.podium_cx, design.podium_cy = c.x, c.y
    res = evaluate_design(design, ctx["receptors"], ctx["times"], ctx["masks"],
                          args.step_min)
    base, base_pass = ctx["base"], ctx["base_pass"]
    pts = ctx["points"]
    flips = [i for i, (r, ok) in enumerate(zip(res, base_pass))
             if ok and not pass_a(r)]
    gains = [i for i, (r, ok) in enumerate(zip(res, base_pass))
             if not ok and pass_a(r)]

    print(f"■ {design.label()}")
    print(f"   높이 {design.height_m:.1f}m · 연면적 {design.gfa_m2:,.0f}㎡"
          f"(용적률 {design.gfa_m2/args.site_area*100:.1f}%) · "
          f"건축면적 {design.coverage_m2():,.0f}㎡"
          f"(건폐율 {design.coverage_m2()/args.site_area*100:.2f}%)")
    print(f"   서비스면적(발코니) {design.service_m2():,.0f}㎡")
    gap = min(ctx["site"].exterior.distance(p.footprint) for p in design.prisms())
    print(f"   대지경계 최소이격 {gap:.1f}m "
          f"(채광이격 1/2 기준 {design.height_m/2:.1f}m → "
          f"{'충족' if gap >= design.height_m/2 else '미충족'})")
    print(f"\n   신규 불충족 {len(flips)}개 / 신규 충족 {len(gains)}개")

    by = defaultdict(list)
    for i, p in enumerate(pts):
        by[(p.jibun, p.school, p.dong)].append(i)
    print("\n" + "=" * 104)
    print(f"{'학교 / 구분':<34}{'수광점':>6}{'기준 충족':>10}{'설계안 충족':>12}"
          f"{'변화':>9}{'신규불충족':>11}{'신규충족':>10}{'평균일조 변화':>15}")
    print("-" * 104)
    rows_md = []
    for key in sorted(by):
        idx = by[key]
        n = len(idx)
        b = sum(1 for i in idx if base_pass[i])
        a = sum(1 for i in idx if pass_a(res[i]))
        nf = sum(1 for i in idx if base_pass[i] and not pass_a(res[i]))
        ng = sum(1 for i in idx if not base_pass[i] and pass_a(res[i]))
        dh = (sum(res[i]["total_h_08_16"] - base[i]["total_h_08_16"]
                  for i in idx) / n)
        label = f"{key[1]} / {key[2]}"
        print(f"{label:<34}{n:>6}{b:>10}{a:>12}{(a-b)/n*100:>+8.1f}%p"
              f"{nf:>11}{ng:>10}{dh:>+14.2f}h")
        rows_md.append((label, n, b, a, nf, ng, dh))
    n = len(pts)
    b = sum(base_pass)
    a = sum(1 for r in res if pass_a(r))
    dh = sum(r["total_h_08_16"] - q["total_h_08_16"]
             for r, q in zip(res, base)) / n
    print("-" * 104)
    print(f"{'전체':<34}{n:>6}{b:>10}{a:>12}{(a-b)/n*100:>+8.1f}%p"
          f"{len(flips):>11}{len(gains):>10}{dh:>+14.2f}h")
    rows_md.append(("전체", n, b, a, len(flips), len(gains), dh))

    export_design(args.outdir, design, ctx, res, args)
    w, h = plan_svg(args.outdir / "plan.svg", ctx, design, flips, gains)
    print(f"\n   배치도 → {args.outdir/'plan.svg'} ({w}x{h})")

    lng, sht = design.dims(design.tower_plate, design.aspect)
    md = ["# 화랑 재건축 – 신규 불충족 0 설계안", "",
          "## 설계 목표", "",
          "현재(대교 신축안 + 화랑 기존)에서 **충족하던 수광점이 하나도 불충족으로",
          "떨어지지 않는** 안. 원래부터 불충족이던 점은 그대로 둔다.", "",
          "## 확정 제원", "", "| 항목 | 값 |", "|---|---|",
          f"| 구성 | {design.floors}층 타워 1개동"
          + (f" + 저층부(1~{design.podium_floors}층) 확대" if design.podium_floors
             else "") + " |",
          f"| 타워 기준층(연면적선) | {design.tower_plate:,.1f}㎡ "
          f"({lng:.2f}×{sht:.2f}m) |"]
    if design.podium_floors:
        plng, psht = design.dims(design.podium_plate, design.podium_aspect)
        md.append(f"| 저층부 기준층(연면적선) | {design.podium_plate:,.1f}㎡ "
                  f"({plng:.2f}×{psht:.2f}m) |")
    md += [
        f"| 높이 | {design.height_m:.1f}m |",
        f"| 연면적 · 용적률 | {design.gfa_m2:,.0f}㎡ · "
        f"{design.gfa_m2/args.site_area*100:.1f}% |",
        f"| 건축면적 · 건폐율 | {design.coverage_m2():,.0f}㎡ · "
        f"{design.coverage_m2()/args.site_area*100:.2f}% |",
        f"| 서비스면적(발코니 1.5m) | {design.service_m2():,.0f}㎡ |",
        f"| 타워 중심 | E{design.cx:,.1f} / N{design.cy:,.1f} |",
        f"| 대지경계 최소이격 | {gap:.1f}m (채광이격 1/2 = "
        f"{design.height_m/2:.1f}m → "
        f"{'충족' if gap >= design.height_m/2 else '미충족'}) |",
        f"| **신규 불충족** | **{len(flips)}개** |",
        f"| 신규 충족 | {len(gains)}개 |",
        "", "## 학교·구분별 전후", "",
        "| 학교 / 구분 | 수광점 | 기준 충족 | 설계안 충족 | 신규 불충족 | "
        "신규 충족 | 평균 일조 변화 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for label, n_, b_, a_, nf_, ng_, dh_ in rows_md:
        bold = "**" if label == "전체" else ""
        md.append(f"| {bold}{label}{bold} | {n_} | {b_} | {a_} | {nf_} | "
                  f"{ng_} | {dh_:+.2f}h |")
    if flips:
        md += ["", "## 남은 신규 불충족 수광점", "",
               "| 수광점 | 학교 | 기준 연속 | 기준 총 | 설계안 연속 | 설계안 총 |",
               "|---|---|---:|---:|---:|---:|"]
        for i in sorted(flips, key=lambda j: res[j]["total_h_08_16"]):
            md.append(f"| {pts[i].pid} | {pts[i].school} | "
                      f"{base[i]['cont_h_08_16']:.2f}h | "
                      f"{base[i]['total_h_08_16']:.2f}h | "
                      f"{res[i]['cont_h_08_16']:.2f}h | "
                      f"{res[i]['total_h_08_16']:.2f}h |")
    md += ["", "## 산출물", "",
           f"- 배치도 `{args.outdir}/plan.svg`",
           f"- 매싱 `{args.outdir}/massing_epsg5186.geojson`",
           f"- 수광점 `{args.outdir}/수광점_전체_epsg5186.geojson` "
           f"(QGIS 스타일 .qml 동봉)",
           f"- 후보 전량 `{args.outdir}/candidates.csv`", ""]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(md), encoding="utf-8")
    print(f"   보고서 → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
