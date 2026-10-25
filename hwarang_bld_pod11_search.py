#!/usr/bin/env python3
# 사용: PYTHONPATH=. python3 hwarang_bld_pod11_search.py <ctx 캐시가 있는 폴더(ctx_sibeom.pkl)>
"""저층부 5층 4,000㎡ + 단변 11m 이상 타워 — 건물 신규 불충족 최소(표적 탐색)."""
import json, math, pickle, sys, random
from pathlib import Path
from shapely.geometry import shape
import hwarang_design_2026 as M
import hwarang_massing_study as H
S = Path(sys.argv[1])
args = M.parse_args(["--buildings", "x", "--rank", "건물", "--floor-h", "3.3",
                     "--dong-gap", "0", "--min-dong-gap", "4", "--floors-min", "38",
                     "--floors-max", "55", "--far-strict", "--min-short", "11",
                     "--outdir", "outputs/hwarang_hr_bld_pod11_2026"])
M.RESI_FLOOR_H, M.RANK_MODE, M.MIN_TOWER_SHORT = 3.3, "건물", 11.0
ctx = pickle.loads((S / "ctx_sibeom.pkl").read_bytes())
ctx["masks"] = H.build_context_masks(ctx["context"], ctx["receptors"], ctx["times"])
# 기준 저층부: 건물 최소안(B)의 저층부를 그대로 쓴다
feats = json.load(open("outputs/hwarang_hr_bld_pod_2026/최적안_매싱_epsg5186.geojson", encoding="utf-8"))["features"]
pod_poly = [shape(f["geometry"]) for f in feats if f["properties"]["kind"] == "저층부 외형선"][0]
mrr = list(pod_poly.minimum_rotated_rectangle.exterior.coords)[:4]
e = [(math.hypot(mrr[(i+1)%4][0]-mrr[i][0], mrr[(i+1)%4][1]-mrr[i][1]),
      math.degrees(math.atan2(mrr[(i+1)%4][0]-mrr[i][0], mrr[(i+1)%4][1]-mrr[i][1])) % 180) for i in range(4)]
paz = max(e)[1]; c = pod_poly.centroid
pod = M.Podium(4000.0, 84.9/47.1, paz, c.x, c.y, 5)
print(f"저층부 중심 E{c.x:.1f} N{c.y:.1f} 방위 {paz:.1f}")
s = M.Search(ctx, args)
designs = []
for f in range(44, 56):
    tp = (ctx["gfa"] - 4000.0*5) / (f - 5)
    for asp in (1.5, 1.8, 2.1, 2.4, 2.7, 3.0):
        if not M.tower_ok(tp, asp):
            continue
        for az in range(0, 180, 10):
            for cx, cy in M.grid_centres(pod.outline(), tp, asp, az, 4.0):
                designs.append(M.Design("단일+저층부", (M.Tower(tp, asp, az, cx, cy, f),), pod))
got = s.sweep("저층부고정 11m", designs, 700)
got += M.refine(s, got, 300, n_seeds=4)
got += M.refine(s, got, 200, n_seeds=3)
b = min(got, key=M.rank)
print("최우수", b["new_fail_bld"], b["new_fail_pg"], b["new_fail"], b["label"], b["design"])
out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
M.write_candidates(out / "candidates.csv", s.rows)
M.export(out, b, ctx, args, "최적안")
(out / "best.json").write_text(json.dumps({k: v for k, v in b.items() if not k.startswith("_")}, ensure_ascii=False, indent=1), encoding="utf-8")
