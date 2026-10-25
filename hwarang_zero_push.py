#!/usr/bin/env python3
"""'신규 불충족 0' 추가 탐색 – 경계를 넓혀 한계를 확인한다.

1차 탐색(hwarang_zero_newfail.py)의 최적안이 탐색 경계에 붙어 나왔다.
    · 저층부 면적이 상한(2,800㎡)
    · 타워 기준층이 하한(330㎡)
둘 다 경계값이면 그 방향으로 더 밀었을 때 더 좋아진다는 뜻이다. 여기서는

    E1  저층부를 더 키우고 타워 기준층 하한을 낮춰 **어디서 포화되는지**
    E2  층수를 39층 아래로 내렸을 때 **신규 불충족 0 이 되는 층수**

를 확인한다. E2 는 사용자가 준 39~55층 범위 밖이지만, 0 이 불가능할 경우
'무엇을 풀어야 0 이 되는지'를 답하기 위해 함께 잰다.
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path
from typing import Any, Sequence

import hwarang_massing_study as H
from hwarang_zero_newfail import (
    Design, PODIUM_FLOORS, count_new_fail, evaluate_design, grid_centres,
    pod_positions, setup,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="신규 불충족 0 추가 탐색")
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
    p.add_argument("--min-plate", type=float, default=180.0,
                   help="타워 기준층 하한(㎡) – 시공 가능성 하한을 낮춰 한계 확인")
    p.add_argument("--probe", action="store_true")
    p.add_argument("--budget-e1", type=int, default=520)
    p.add_argument("--budget-e2", type=int, default=260)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    ctx = setup(args)
    receptors, base_pass = ctx["receptors"], ctx["base_pass"]
    site_az, _ = H.site_axes(ctx["site"])
    gfa = ctx["gfa"]
    print(f"■ 준비 완료 · 수광점 {len(receptors)} · 기준 충족 {sum(base_pass)}",
          flush=True)

    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def run(d: Design, tag: str):
        key = (d.floors, round(d.aspect, 2), round(d.azimuth, 1),
               round(d.cx, 1), round(d.cy, 1), round(d.podium_plate, 1),
               round(d.podium_aspect, 2), round(d.podium_cx, 1),
               round(d.podium_cy, 1))
        if key in seen:
            return None
        seen.add(key)
        if d.coverage_m2() > ctx["max_cover"]:
            return None
        for _n, poly in d.outlines():
            if not ctx["envelope"].contains(poly):
                return None
        res = evaluate_design(d, receptors, ctx["times"], ctx["masks"],
                              args.step_min)
        nf, ng, _ = count_new_fail(res, base_pass)
        lng, sht = d.dims(d.tower_plate, d.aspect)
        row = {"stage": tag, "floors": d.floors, "aspect": d.aspect,
               "azimuth": d.azimuth, "x": round(d.cx, 1), "y": round(d.cy, 1),
               "tower_plate": round(d.tower_plate, 1),
               "tower_long": round(lng, 2), "tower_short": round(sht, 2),
               "podium_plate": round(d.podium_plate, 1),
               "podium_aspect": d.podium_aspect,
               "podium_x": round(d.podium_cx, 1),
               "podium_y": round(d.podium_cy, 1),
               "new_fail": nf, "new_ok": ng,
               "coverage_m2": round(d.coverage_m2(), 1),
               "bcr_pct": round(d.coverage_m2() / args.site_area * 100, 2),
               "far_pct": round(d.gfa_m2 / args.site_area * 100, 1),
               "height_m": round(d.height_m, 2), "_design": d}
        rows.append(row)
        return row

    def sweep(tag: str, designs, budget: int):
        designs = list(designs)
        if len(designs) > budget:
            stride = len(designs) / budget
            designs = [designs[int(i * stride)] for i in range(budget)]
        t = time.time()
        print(f"■ {tag}: {len(designs)}개 평가 중…", flush=True)
        got = [r for d in designs if (r := run(d, tag))]
        print(f"■ {tag}: {len(got)}개 · {time.time()-t:.0f}s", flush=True)
        if got:
            b = min(got, key=lambda r: (r["new_fail"], -r["floors"]))
            print(f"   최소 신규 불충족 {b['new_fail']}개 — {b['_design'].label()}",
                  flush=True)
        return got

    # ── E1: 저층부 상한·타워 하한을 넓힌 정밀 탐색 ────────────────────────
    e1 = []
    for floors in (39, 41, 43, 45):
        for pod in (2400.0, 2800.0, 3000.0, 3200.0, 3400.0, 3600.0):
            tp = (gfa - pod * PODIUM_FLOORS) / (floors - PODIUM_FLOORS)
            if tp < args.min_plate:
                continue
            spots = pod_positions(ctx["envelope"], pod, site_az, 14.0)
            for aspect in (2.0, 2.5, 3.0, 3.5):
                for az in (150.0, 157.5, 165.0, 172.5):
                    for cx, cy in grid_centres(ctx["envelope"], tp, aspect, az,
                                               14.0):
                        for pa, px, py in spots:
                            e1.append(Design(floors, aspect, az, cx, cy, tp,
                                             PODIUM_FLOORS, pod, pa, px, py,
                                             site_az))
    sweep("E1 저층부 확대(경계 확장)", e1, args.budget_e1)

    # ── E2: 층수를 낮추면 어디서 0 이 되는가(범위 밖 참고) ────────────────
    e2 = []
    for floors in (20, 25, 30, 35, 39):
        for pod in (0.0, 2000.0, 2800.0, 3400.0):
            pf = PODIUM_FLOORS if pod > 0 and floors > PODIUM_FLOORS + 4 else 0
            tp = (gfa - pod * pf) / (floors - pf)
            if tp < args.min_plate:
                continue
            spots = (pod_positions(ctx["envelope"], pod, site_az, 16.0)
                     if pf else [(0.0, 0.0, 0.0)])
            for aspect in (2.0, 2.5, 3.0):
                for az in (150.0, 165.0):
                    for cx, cy in grid_centres(ctx["envelope"], tp, aspect, az,
                                               16.0):
                        for pa, px, py in spots:
                            e2.append(Design(floors, aspect, az, cx, cy, tp,
                                             pf, pod, pa, px, py, site_az))
    sweep("E2 층수 하향(범위 밖 참고)", e2, args.budget_e2)

    out = args.outdir / "push_candidates.csv"
    cols = [c for c in rows[0] if not c.startswith("_")] if rows else []
    with out.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(cols)
        for r in sorted(rows, key=lambda q: (q["new_fail"], -q["floors"])):
            w.writerow([r[c] for c in cols])
    print(f"\n후보 {len(rows)}개 → {out}")

    print("\n■ 층수·저층부별 최소 신규 불충족")
    grid: dict[tuple, int] = {}
    for r in rows:
        k = (r["floors"], r["podium_plate"])
        grid[k] = min(grid.get(k, 10**9), r["new_fail"])
    print(f"{'층수':>5}{'저층부㎡':>10}{'타워㎡':>9}{'최소 신규불충족':>16}")
    for (fl, pod) in sorted(grid):
        tp = next(r["tower_plate"] for r in rows
                  if r["floors"] == fl and r["podium_plate"] == pod)
        print(f"{fl:>5}{pod:>10,.0f}{tp:>9,.0f}{grid[(fl, pod)]:>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
