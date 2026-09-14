#!/usr/bin/env python3
"""화랑 설계안 – 채광 이격 규정을 만족하는 범위 안에서의 일조 최적안.

건축법 시행령 제86조 제3항 제1호(준주거지역 공동주택)
    채광창이 있는 벽면에서 그 벽면의 직각 방향으로 인접 대지경계선까지의
    수평거리의 **4배** 이하로 건축물 각 부분의 높이를 제한한다.

앞선 탐색(hwarang_redesign.py)은 이 규정을 순위에 넣지 않았기 때문에,
일조 최적 위치(대지 동측)가 규정을 만족하지 못하는 문제가 있었다.
여기서는 **규정을 만족하는 위치만** 후보로 두고 층수별 일조 최적을 찾는다.

즉 우선순위가 이렇게 바뀐다.
    0. 채광 이격 규정 충족 (법적 전제)
    1. 일조권 충족  2. 층수 고층화  3. 최대 용적률  4. 한강뷰
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import LineString, Point

import hwarang_massing_study as H
from hwarang_redesign import (
    BALCONY_M, ViewModel, build_design, metrics_of, outline_polygon,
    plate_polygon, rank_key, river_azimuths, setup, stride_sample,
)


def facade_gap(site, plate_area: float, aspect: float, azimuth: float,
               cx: float, cy: float, samples: int = 11,
               balcony: float = BALCONY_M) -> float:
    """장변(채광창면) 직각 방향으로 대지경계선까지의 수평거리(양쪽 중 최소).

    기준면은 **외형선(발코니 끝)** 이다. 발코니가 있으면 채광창면이 그만큼
    대지경계선 쪽으로 나오므로 이격이 발코니 깊이만큼 줄어든다.
    단변(측벽)은 채광창이 없는 것으로 보아 규정 대상에서 제외한다.
    """
    short = math.sqrt(plate_area / aspect) + 2 * balcony
    lng = plate_area / math.sqrt(plate_area / aspect) + 2 * balcony
    ux, uy = math.sin(math.radians(azimuth)), math.cos(math.radians(azimuth))
    worst = float("inf")
    for sign in (1, -1):
        nz = math.radians(azimuth + 90 * sign)
        nx, ny = math.sin(nz), math.cos(nz)
        for i in range(samples):
            t = i / (samples - 1) * lng - lng / 2
            px = cx + ux * t + nx * short / 2
            py = cy + uy * t + ny * short / 2
            ray = LineString([(px, py), (px + nx * 400, py + ny * 400)])
            inter = ray.intersection(site.exterior)
            if inter.is_empty:
                return 0.0
            pts = [inter] if inter.geom_type == "Point" else list(inter.geoms)
            d = min(Point(px, py).distance(p) for p in pts if p.geom_type == "Point")
            worst = min(worst, d)
    return 0.0 if worst == float("inf") else worst


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="채광 이격 충족 범위 내 일조 최적안")
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
    p.add_argument("--sample", type=int, default=3)
    p.add_argument("--multiple", type=float, default=4.0,
                   help="채광 이격 배수(준주거 4배, 일반 2배)")
    p.add_argument("--floors", type=int, nargs="*",
                   default=[45, 50, 55, 60, 62, 65, 68, 70])
    p.add_argument("--aspects", type=float, nargs="*", default=[1.5, 2.0, 2.5])
    p.add_argument("--azimuths", type=float, nargs="*", default=[150, 165, 180])
    p.add_argument("--grid", type=float, default=4.0)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    ctx = setup(args)
    site, envelope = ctx["site"], ctx["envelope"]
    receptors, times, context = ctx["receptors"], ctx["times"], ctx["context"]
    gfa = ctx["gfa"]
    horiz = {label: info["kind"] == "운동장" for label, info in ctx["units"].items()}
    view = ViewModel(context, river_azimuths(args.river_from, args.river_to),
                     args.view_reach)

    sample = stride_sample(receptors, args.sample)
    masks_s = H.build_context_masks(context, sample, times)
    masks_f = H.build_context_masks(context, receptors, times)

    base_now = metrics_of(H.evaluate(receptors, ctx["existing"], times, masks_f,
                                     args.step_min), horiz)
    base_none = metrics_of(H.evaluate(receptors, [], times, masks_f,
                                      args.step_min), horiz)
    print(f"기준선: 화랑없음 {base_none['pass_pct']:.1f}% / "
          f"기존 10층 3개동 {base_now['pass_pct']:.1f}%")
    print(f"채광 이격 기준: 높이 ≤ 장변직각 이격 × {args.multiple:g}배\n")

    rows: list[dict[str, Any]] = []
    for F in args.floors:
        plate = gfa / F
        if plate > ctx["max_footprint"]:
            continue
        height = F * H.RESI_FLOOR_H + H.ROOFTOP_M
        need = height / args.multiple
        best = None
        n_ok = 0
        for asp in args.aspects:
            for az in args.azimuths:
                bx0, by0, bx1, by1 = envelope.bounds
                y = by0
                while y <= by1:
                    x = bx0
                    while x <= bx1:
                        poly = outline_polygon(plate, asp, az, x, y)
                        if envelope.contains(poly):
                            if facade_gap(site, plate, asp, az, x, y) >= need:
                                n_ok += 1
                                d = build_design(1, F, asp, az, [(x, y)], gfa)
                                m = metrics_of(H.evaluate(sample, d.prisms, times,
                                                          masks_s, args.step_min),
                                               horiz)
                                v = view.openness(x, y, F)
                                k = rank_key(m, d, v)
                                if best is None or k < best[0]:
                                    best = (k, d, m, v)
                        x += args.grid
                    y += args.grid
        if best is None:
            print(f"{F:>3}층(판 {plate:>5.0f}㎡, 높이 {height:6.1f}m, "
                  f"필요이격 {need:5.1f}m): 충족 위치 없음")
            continue
        _, d, m, v = best
        rows.append({"design": d, "m": m, "view": v, "n_ok": n_ok, "need": need})
        print(f"{F:>3}층(판 {plate:>5.0f}㎡, 높이 {height:6.1f}m, "
              f"필요이격 {need:5.1f}m): 충족위치 {n_ok:4d}개, "
              f"최적 {m['pass_pct']:.1f}% ({d.aspect:g}:1·방위{d.azimuth:g}°)")

    if not rows:
        print("\n채광 이격을 만족하는 구성이 없습니다.")
        return 1

    print(f"\n■ 전체 수광점 정밀 재평가")
    print(f"{'층수':>5}{'종횡비':>7}{'방위':>6}{'기준층㎡':>9}{'장×단(m)':>15}"
          f"{'교사동A':>9}{'불충족h':>9}{'조망':>7}{'이격':>7}")
    print("-" * 78)
    final = []
    for r in rows:
        d = r["design"]
        m = metrics_of(H.evaluate(receptors, d.prisms, times, masks_f,
                                  args.step_min), horiz)
        v = view.openness(*d.positions[0], d.floors)
        gap = facade_gap(site, d.plate_m2, d.aspect, d.azimuth, *d.positions[0])
        short = math.sqrt(d.plate_m2 / d.aspect)
        final.append({"design": d, "m": m, "view": v, "gap": gap,
                      "key": rank_key(m, d, v)})
        print(f"{d.floors:>5}{d.aspect:>7g}{d.azimuth:>6g}{d.plate_m2:>9.0f}"
              f"{short*d.aspect:>9.1f}×{short:>5.1f}{m['pass_pct']:>8.1f}%"
              f"{m['fail_mean_h']:>9.2f}{v:>6.1f}%{gap:>6.1f}m")

    final.sort(key=lambda r: r["key"])
    b = final[0]
    d = b["design"]
    short = math.sqrt(d.plate_m2 / d.aspect)
    print(f"\n■ 법적 이격을 만족하는 최적안: {d.label()}")
    print(f"   기준층 {d.plate_m2:,.0f}㎡ ({short*d.aspect:.2f}×{short:.2f}m) · "
          f"높이 {d.height_m:.1f}m")
    print(f"   중심 E{d.positions[0][0]:,.1f} / N{d.positions[0][1]:,.1f} · "
          f"장변직각 이격 {b['gap']:.1f}m (필요 {d.height_m/args.multiple:.1f}m)")
    print(f"   교사동 기준A {b['m']['pass_pct']:.1f}% "
          f"(기존 {base_now['pass_pct']:.1f}% 대비 "
          f"{b['m']['pass_pct']-base_now['pass_pct']:+.1f}%p) · "
          f"한강조망 {b['view']:.1f}%")

    with (args.outdir / "hwarang_legal_optimum.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["층수", "종횡비", "방위", "기준층㎡", "장변m", "단변m", "높이m",
                    "장변직각이격m", "필요이격m", "교사동기준A%", "불충족평균h",
                    "교실평균h", "운동장기준A%", "한강조망%", "중심X", "중심Y"])
        for r in final:
            dd = r["design"]
            s = math.sqrt(dd.plate_m2 / dd.aspect)
            w.writerow([dd.floors, dd.aspect, dd.azimuth, round(dd.plate_m2, 1),
                        round(s * dd.aspect, 2), round(s, 2), round(dd.height_m, 1),
                        round(r["gap"], 1), round(dd.height_m / args.multiple, 1),
                        round(r["m"]["pass_pct"], 1), round(r["m"]["fail_mean_h"], 2),
                        round(r["m"]["mean_total_h"], 2),
                        round(r["m"]["pg_pass_pct"], 1), round(r["view"], 1),
                        round(dd.positions[0][0], 1), round(dd.positions[0][1], 1)])
    print(f"\n저장: {args.outdir}/hwarang_legal_optimum.csv")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
