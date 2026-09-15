#!/usr/bin/env python3
"""'신규 불충족 0' – 정밀 미세조정과 용적률 민감도.

1·2차 탐색으로 39~55층·용적률 400% 안에서 신규 불충족이 **27개** 까지 내려왔고,
그 27개는 전부 '연속 2시간'에서 걸린다. 특히 10개는 연속 1.83h 로 **10분**
(계산 간격 1스텝) 모자란다. 그래서

    F  최적안 주변을 방위 1.5° · 위치 2m 간격으로 정밀 재탐색
    G  용적률을 낮추면 신규 불충족 0 이 어디서 되는지(요구조건 밖 참고)

를 잰다. G 는 사용자가 준 400% 밖이지만, 0 이 불가능할 경우 '무엇을 풀어야
0 이 되는지' 를 답하기 위한 것이다.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any, Sequence

import hwarang_massing_study as H
from hwarang_zero_newfail import (
    Design, PODIUM_FLOORS, count_new_fail, evaluate_design, grid_centres,
    pod_positions, setup,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="정밀 미세조정·용적률 민감도")
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
    p.add_argument("--budget-f", type=int, default=620)
    p.add_argument("--budget-g", type=int, default=300)
    p.add_argument("--probe", action="store_true")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    ctx = setup(args)
    receptors, base_pass = ctx["receptors"], ctx["base_pass"]
    site_az, _ = H.site_axes(ctx["site"])
    print(f"■ 준비 완료 · 수광점 {len(receptors)} · 기준 충족 {sum(base_pass)}",
          flush=True)

    best0 = json.loads((args.outdir / "best.json").read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def run(d: Design, tag: str):
        key = (d.floors, round(d.aspect, 3), round(d.azimuth, 2),
               round(d.cx, 1), round(d.cy, 1), round(d.podium_plate, 1),
               round(d.podium_aspect, 2), round(d.podium_cx, 1),
               round(d.podium_cy, 1), round(d.gfa_m2))
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
        row = {"stage": tag, "floors": d.floors, "aspect": round(d.aspect, 3),
               "azimuth": d.azimuth, "x": round(d.cx, 1), "y": round(d.cy, 1),
               "tower_plate": round(d.tower_plate, 1),
               "tower_long": round(lng, 2), "tower_short": round(sht, 2),
               "podium_floors": d.podium_floors,
               "podium_plate": round(d.podium_plate, 1),
               "podium_aspect": d.podium_aspect,
               "podium_x": round(d.podium_cx, 1),
               "podium_y": round(d.podium_cy, 1),
               "podium_az": round(d.podium_azimuth, 1),
               "new_fail": nf, "new_ok": ng,
               "coverage_m2": round(d.coverage_m2(), 1),
               "bcr_pct": round(d.coverage_m2() / args.site_area * 100, 2),
               "gfa_m2": round(d.gfa_m2), "far_pct": round(
                   d.gfa_m2 / args.site_area * 100, 1),
               "height_m": round(d.height_m, 2), "_design": d, "_res": res}
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

    # ── F: 최적안 주변 정밀 재탐색 ────────────────────────────────────────
    pod = float(best0["podium_plate"])
    pa = float(best0["podium_aspect"])
    px, py = float(best0["podium_x"]), float(best0["podium_y"])
    f_designs = []
    for floors in (39, 40, 41, 42, 43):
        tp = (ctx["gfa"] - pod * PODIUM_FLOORS) / (floors - PODIUM_FLOORS)
        if tp < 180.0:
            continue
        for aspect in (best0["aspect"] * k
                       for k in (0.85, 0.95, 1.0, 1.1, 1.25, 1.45)):
            for daz in (-6.0, -4.5, -3.0, -1.5, 0.0, 1.5, 3.0, 4.5, 6.0):
                az = (float(best0["azimuth"]) + daz) % 180
                for dx in (-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0):
                    for dy in (-6.0, -4.0, -2.0, 0.0, 2.0, 4.0, 6.0):
                        f_designs.append(Design(
                            floors, aspect, az, float(best0["x"]) + dx,
                            float(best0["y"]) + dy, tp, PODIUM_FLOORS, pod,
                            pa, px, py, site_az))
    f_rows = sweep("F 정밀 미세조정", f_designs, args.budget_f)

    # ── G: 용적률 민감도(요구조건 밖 참고) ───────────────────────────────
    g_designs = []
    for far in (150.0, 200.0, 250.0, 300.0, 350.0):
        gfa = args.site_area * far / 100.0
        for floors in (39, 45):
            for pod_plate in (0.0, 1500.0, 2200.0, 2800.0):
                pf = PODIUM_FLOORS if pod_plate > 0 else 0
                tp = (gfa - pod_plate * pf) / (floors - pf)
                if tp < 180.0:
                    continue
                spots = (pod_positions(ctx["envelope"], pod_plate, site_az, 18.0)
                         if pf else [(0.0, 0.0, 0.0)])
                if pf and not spots:
                    continue
                for aspect in (2.0, 3.0):
                    for az in (150.0, 165.0):
                        for cx, cy in grid_centres(ctx["envelope"], tp, aspect,
                                                   az, 18.0):
                            spa, sx, sy = spots[0]
                            g_designs.append(Design(floors, aspect, az, cx, cy,
                                                    tp, pf, pod_plate, spa,
                                                    sx, sy, site_az))
    g_rows = sweep("G 용적률 민감도", g_designs, args.budget_g)

    out = args.outdir / "final_candidates.csv"
    cols = [c for c in rows[0] if not c.startswith("_")] if rows else []
    with out.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(cols)
        for r in sorted(rows, key=lambda q: (q["new_fail"], -q["floors"])):
            w.writerow([r[c] for c in cols])
    print(f"\n후보 {len(rows)}개 → {out}", flush=True)

    print("\n■ 용적률별 최소 신규 불충족")
    per: dict[float, int] = {}
    for r in g_rows:
        per[r["far_pct"]] = min(per.get(r["far_pct"], 10**9), r["new_fail"])
    for far in sorted(per):
        print(f"   용적률 {far:>5.0f}% → 최소 신규 불충족 {per[far]:>3}개")

    if f_rows:
        bf = min(f_rows, key=lambda r: (r["new_fail"], -r["floors"]))
        if bf["new_fail"] < int(best0["new_fail"]):
            (args.outdir / "best.json").write_text(json.dumps(
                {k: v for k, v in bf.items() if not k.startswith("_")},
                ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"\n■ best.json 갱신: {best0['new_fail']} → {bf['new_fail']}개")
        else:
            print(f"\n■ 개선 없음(기존 {best0['new_fail']}개 유지)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
