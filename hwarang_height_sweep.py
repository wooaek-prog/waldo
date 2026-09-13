#!/usr/bin/env python3
"""화랑 설계안 – 층수 상한 확장 검증.

본 탐색(hwarang_redesign.py)의 층수 범위가 35~80층이라 최적값이 상한
경계(80층)에서 나왔다. 용적률 400% 고정에서 층수를 더 올리면 기준층이
더 작아져 그림자 폭이 좁아지는데, 그 이득이 어디서 꺾이는지 확인한다.

같은 형상·방위·위치를 유지한 채 층수만 바꿔 전체 수광점으로 평가한다.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Sequence

import hwarang_massing_study as H
from hwarang_redesign import (
    ViewModel, build_design, metrics_of, plate_polygon, river_azimuths, setup,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="화랑 층수 상한 확장 검증")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_redesign"))
    p.add_argument("--site-area", type=float, default=9395.0)
    p.add_argument("--far", type=float, default=400.0)
    p.add_argument("--bcr", type=float, default=60.0)
    p.add_argument("--setback", type=float, default=3.0)
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--river-from", type=float, default=340.0)
    p.add_argument("--river-to", type=float, default=110.0)
    p.add_argument("--view-reach", type=float, default=600.0)
    p.add_argument("--aspect", type=float, required=True)
    p.add_argument("--azimuth", type=float, required=True)
    p.add_argument("--x", type=float, required=True)
    p.add_argument("--y", type=float, required=True)
    p.add_argument("--floors", type=int, nargs="*",
                   default=[40, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 110, 120])
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    ctx = setup(args)
    receptors, times, context = ctx["receptors"], ctx["times"], ctx["context"]
    gfa, envelope = ctx["gfa"], ctx["envelope"]
    masks = H.build_context_masks(context, receptors, times)
    horiz = {label: info["kind"] == "운동장" for label, info in ctx["units"].items()}
    view = ViewModel(context, river_azimuths(args.river_from, args.river_to),
                     args.view_reach)

    base_now = metrics_of(H.evaluate(receptors, ctx["existing"], times, masks,
                                     args.step_min), horiz)
    base_none = metrics_of(H.evaluate(receptors, [], times, masks, args.step_min),
                           horiz)
    print(f"기준선: 화랑없음 {base_none['pass_pct']:.1f}% / "
          f"기존 10층 3개동 {base_now['pass_pct']:.1f}%")
    print(f"형상 {args.aspect:g}:1 · 방위 {args.azimuth:g}° · "
          f"중심 E{args.x:,.1f}/N{args.y:,.1f}\n")

    print(f"{'층수':>5}{'기준층㎡':>9}{'장×단(m)':>16}{'높이m':>8}{'교사동A':>9}"
          f"{'불충족h':>9}{'교실평균h':>10}{'운동장A':>9}{'조망':>7}{'이격':>7}")
    print("-" * 92)
    rows = []
    for F in args.floors:
        plate = gfa / F
        if plate > ctx["max_footprint"]:
            continue
        poly = plate_polygon(plate, args.aspect, args.azimuth, args.x, args.y)
        fits = envelope.contains(poly)
        d = build_design(1, F, args.aspect, args.azimuth, [(args.x, args.y)], gfa)
        m = metrics_of(H.evaluate(receptors, d.prisms, times, masks, args.step_min),
                       horiz)
        v = view.openness(args.x, args.y, F)
        gap = ctx["site"].exterior.distance(poly)
        short = math.sqrt(plate / args.aspect)
        rows.append((F, plate, short * args.aspect, short, d.height_m, m, v, gap, fits))
        flag = "" if fits else "  ※이격선 초과"
        print(f"{F:>5}{plate:>9.0f}{d.height_m*0+short*args.aspect:>9.1f}×{short:>5.1f}"
              f"{d.height_m:>8.1f}{m['pass_pct']:>8.1f}%{m['fail_mean_h']:>9.2f}"
              f"{m['mean_total_h']:>10.2f}{m['pg_pass_pct']:>8.1f}%{v:>6.1f}%"
              f"{gap:>6.1f}m{flag}")

    best = max(rows, key=lambda r: (round(r[5]["pass_pct"], 1),
                                    round(r[5]["fail_mean_h"], 2), r[0]))
    print(f"\n■ 일조 최적 층수: {best[0]}층 (기준층 {best[1]:.0f}㎡, "
          f"교사동 기준A {best[5]['pass_pct']:.1f}%)")
    plateau = [r for r in rows if round(r[5]["pass_pct"], 1)
               >= round(best[5]["pass_pct"], 1) - 0.1]
    if plateau:
        print(f"   동등구간(−0.1%p 이내): "
              f"{min(r[0] for r in plateau)}~{max(r[0] for r in plateau)}층")

    with (args.outdir / "hwarang_height_sweep.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["층수", "기준층㎡", "장변m", "단변m", "높이m", "교사동기준A%",
                    "불충족평균h", "교실평균h", "운동장기준A%", "한강조망%",
                    "대지경계이격m", "이격선내"])
        for F, plate, lng, sht, hgt, m, v, gap, fits in rows:
            w.writerow([F, round(plate, 1), round(lng, 2), round(sht, 2),
                        round(hgt, 1), round(m["pass_pct"], 1),
                        round(m["fail_mean_h"], 2), round(m["mean_total_h"], 2),
                        round(m["pg_pass_pct"], 1), round(v, 1), round(gap, 1),
                        "예" if fits else "아니오"])
    print(f"\n저장: {args.outdir}/hwarang_height_sweep.csv")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
