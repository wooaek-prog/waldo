#!/usr/bin/env python3
"""화랑 – 20층 4개동 · 2x2 배치 · 2개동씩 붙이고 가운데 통로 최대화.

`hwarang_design_2026.py` 의 `family_multi` 는 4개동을 균등 피치로 한 줄
또는 격자에 늘어놓는다. 이번 요청은 그와 다르다 — **2개동씩 짝지어 바짝
붙이고(작은 간격), 그 두 쌍 사이는 최대한 벌려 가운데를 통로로 쓴다.**
간격이 두 종류(짝 안 간격 `pair_gap`, 두 쌍 사이 간격 `corridor_gap`)로
갈리는 비대칭 2x2라 별도 스크립트로 뺐다.

기하:
    타워 4개, 모두 같은 크기(기준층 = 연면적 ÷ 80, 20층).
    긴 변 방위 az 를 축으로 두 개의 국소축을 쓴다.
        U(=az 방향)  — 두 짝(클러스터)을 이 축으로 벌린다 → corridor_gap
        V(=az+90)    — 한 짝 안의 두 동을 이 축으로 붙인다 → pair_gap
    즉 짝 안에서는 장변끼리 마주보고(작게), 두 짝 사이는 단변끼리
    마주본다(크게) — 실제 그림처럼 격자 2x2 모양이 나온다.

    python3 hwarang_2x2_paired.py --buildings <AL_D010.gpkg> \\
        --cache <ctx.pkl> --outdir outputs/hwarang_20f_2x2_2026
"""

from __future__ import annotations

import argparse
import math
import pickle
import time
from pathlib import Path
from typing import Any, Sequence

import hwarang_design_2026 as M
import hwarang_massing_study as H
from hwarang_redesign import BALCONY_M

PAIR_GAPS = (4.0, 5.0, 6.0)
ASPECTS = (1.0, 1.3, 1.6, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5)
AZ_STEP = 15.0
SX_OFFSETS = (-15.0, -7.5, 0.0, 7.5, 15.0)   # 짝축 방향으로 전체를 밀기
SY_OFFSETS = (-10.0, 0.0, 10.0)              # 통로축 방향으로 전체를 밀기

# 어느 변을 통로에 붙이느냐로 두 형(型)이 갈린다.
#   swap=False  통로축 = az(장변) 방향  → 클러스터가 **단변끼리** 마주본다
#               짝축   = az+90(단변) 방향 → 짝 안 두 동은 **장변끼리** 마주본다
#               (판 옆구리를 붙인 쌍 두 개를 판 길이 방향으로 벌린다)
#   swap=True   통로축 = az+90 방향     → 클러스터가 **장변끼리** 마주본다
#               짝축   = az(장변) 방향   → 짝 안 두 동은 **단변끼리** 마주본다
#               (판 두 개를 거의 일자로 이어 붙인 '줄'을 옆으로 벌린다)
# 통로 폭은 클러스터가 통로 방향으로 차지하는 폭(=마주보는 변의 길이)이
# 작을수록 커진다 — 어느 쪽이 유리한지는 대지 형상에 달려 있어 둘 다 훑는다.


def tower_centers(plate: float, aspect: float, az: float, pair_gap: float,
                  corridor_gap: float, center: tuple[float, float],
                  swap: bool) -> list[tuple[float, float]]:
    short = math.sqrt(plate / aspect)
    lng = plate / short
    corridor_extent, pair_extent = (short, lng) if swap else (lng, short)
    half_c = (corridor_extent + 2 * BALCONY_M) / 2 + corridor_gap / 2
    half_p = (pair_extent + 2 * BALCONY_M) / 2 + pair_gap / 2
    d1x, d1y = math.sin(math.radians(az)), math.cos(math.radians(az))   # az
    d2x, d2y = math.cos(math.radians(az)), -math.sin(math.radians(az))  # az+90
    cdx, cdy, pdx, pdy = (d2x, d2y, d1x, d1y) if swap else (d1x, d1y, d2x, d2y)
    out = []
    for sc in (-1, 1):
        for sp in (-1, 1):
            cx = center[0] + cdx * sc * half_c + pdx * sp * half_p
            cy = center[1] + cdy * sc * half_c + pdy * sp * half_p
            out.append((cx, cy))
    return out


def make_design(plate: float, aspect: float, az: float, pair_gap: float,
                corridor_gap: float, center: tuple[float, float], floors: int,
                swap: bool = False) -> M.Design:
    towers = tuple(M.Tower(plate, aspect, az, cx, cy, floors)
                   for cx, cy in tower_centers(plate, aspect, az, pair_gap,
                                               corridor_gap, center, swap))
    return M.Design("2x2쌍", towers)


def max_corridor_gap(envelope, plate: float, aspect: float, az: float,
                     pair_gap: float, center: tuple[float, float],
                     floors: int, swap: bool = False, lo: float = 4.0,
                     hi: float = 220.0) -> float | None:
    """이 배치(방위·짝간격·중심·형)에서 대지 안에 들어가는 최대 통로 폭.

    corridor_gap 을 늘릴수록 클러스터가 대지 밖으로 나가는 방향으로만
    움직인다(포함 여부가 단조 감소) — 이분탐색으로 찾는다.
    """
    def fits(g: float) -> bool:
        d = make_design(plate, aspect, az, pair_gap, g, center, floors, swap)
        return all(envelope.contains(p) for _n, p, _h in d.outlines())

    if not fits(lo):
        return None
    if fits(hi):
        return hi          # 상한에 닿음 — 이 대지 규모에선 사실상 없음
    for _ in range(40):
        mid = (lo + hi) / 2
        if fits(mid):
            lo = mid
        else:
            hi = mid
    return lo


def search_max_corridor(ctx: dict[str, Any], args) -> list[dict[str, Any]]:
    """기하만으로(일조 계산 없이) 통로 폭을 최대화하는 배치를 찾는다."""
    envelope = ctx["envelope"]
    ecx, ecy = envelope.centroid.x, envelope.centroid.y
    site_az, _ = H.site_axes(ctx["site"])
    plate = ctx["gfa"] / (4 * args.floors)
    azs = [(site_az + a) % 180 for a in range(0, 180, int(AZ_STEP))]
    out = []
    for az in azs:
        d1x, d1y = math.sin(math.radians(az)), math.cos(math.radians(az))
        d2x, d2y = math.cos(math.radians(az)), -math.sin(math.radians(az))
        for aspect in ASPECTS:
            if not M.tower_ok(plate, aspect):
                continue
            for pair_gap in PAIR_GAPS:
                for swap in (False, True):
                    # sx,sy 는 항상 (짝축, 통로축) 오프셋으로 해석한다
                    pdx, pdy, cdx, cdy = (
                        (d1x, d1y, d2x, d2y) if swap else (d2x, d2y, d1x, d1y))
                    for sx in SX_OFFSETS:
                        for sy in SY_OFFSETS:
                            center = (ecx + pdx * sx + cdx * sy,
                                     ecy + pdy * sx + cdy * sy)
                            g = max_corridor_gap(envelope, plate, aspect, az,
                                                 pair_gap, center,
                                                 args.floors, swap)
                            if g is None:
                                continue
                            out.append({"az": az, "aspect": aspect,
                                       "pair_gap": pair_gap, "center": center,
                                       "corridor_gap": g, "plate": plate,
                                       "swap": swap})
    return out


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--cache", type=Path, default=None)
    p.add_argument("--outdir", type=Path,
                   default=Path("outputs/hwarang_20f_2x2_2026"))
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
    p.add_argument("--min-plate", type=float, default=M.MIN_TOWER_PLATE)
    p.add_argument("--min-short", type=float, default=M.MIN_TOWER_SHORT)
    p.add_argument("--floors", type=int, default=20)
    p.add_argument("--floor-h", type=float, default=3.3)
    p.add_argument("--min-dong-gap", type=float, default=4.0)
    p.add_argument("--daylight-multiple", type=float, default=4.0)
    p.add_argument("--n-eval", type=int, default=40,
                   help="기하상 통로가 넓은 상위 몇 개를 일조까지 평가할지")
    p.add_argument("--n-table", type=int, default=8,
                   help="트레이드오프 표에 넣을 통로폭 구간 수")
    a = p.parse_args(argv)
    a.podium_floors, a.podium_plates = [], []
    a.floors_min = a.floors_max = a.floors
    a.dong_gap = 0.0
    a.require_daylight = False
    a.no_podium = True

    M.MIN_TOWER_PLATE, M.MIN_TOWER_SHORT = a.min_plate, a.min_short
    M.RESI_FLOOR_H = a.floor_h
    M.RANK_MODE = "일조"
    a.outdir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    if a.cache and a.cache.exists():
        ctx = pickle.loads(a.cache.read_bytes())
        ctx["masks"] = H.build_context_masks(ctx["context"], ctx["receptors"],
                                             ctx["times"])
        print(f"■ 준비(캐시) {time.time()-t0:.0f}s")
    else:
        ctx = M.setup(a)
        print(f"■ 준비 {time.time()-t0:.0f}s")
    base_pass = ctx["base_pass"]
    print(f"   수광점 {len(ctx['receptors'])}개 · 기준 충족 {sum(base_pass)} / "
          f"불충족 {len(base_pass)-sum(base_pass)}")
    print(f"   대지 {ctx['site'].area:,.0f}㎡ · 연면적 목표 {ctx['gfa']:,.0f}㎡")

    print("\n■ 1단계 — 기하만으로 통로 폭 최대화(일조 계산 없음)")
    t0 = time.time()
    geo = search_max_corridor(ctx, a)
    print(f"   배치 가능 {len(geo)}개 · {time.time()-t0:.0f}s")
    if not geo:
        raise SystemExit("이 조건으로는 배치 가능한 2x2 쌍이 없습니다.")
    geo.sort(key=lambda r: -r["corridor_gap"])
    gmax = geo[0]["corridor_gap"]
    shape = "장변 마주(줄을 옆으로 벌림)" if geo[0]["swap"] else "단변 마주(판을 길이로 벌림)"
    print(f"   최대 통로 폭 {gmax:.1f}m (방위 {geo[0]['az']:.0f}° · "
          f"세장비 {geo[0]['aspect']:g}:1 · 짝간격 {geo[0]['pair_gap']:.0f}m · {shape})")

    s = M.Search(ctx, a)

    # 통로 폭 하나로만 상위를 뽑으면 특정 방위·형(swap) 하나가 결과를 독식한다
    # (판이 짧아지는 형이 통로에 절대적으로 유리해서다). 방위·형 조합별로
    # 최우수 1~2개씩 뽑아 다양성을 확보한 뒤 일조를 평가한다.
    print(f"\n■ 2단계 — 방위·형별 통로 최대안 일조 평가")
    seen: dict[tuple, list] = {}
    for r in geo:
        k = (round(r["az"] / 5) * 5, r["swap"])
        seen.setdefault(k, []).append(r)
    to_eval = []
    for k, lst in seen.items():
        to_eval += sorted(lst, key=lambda r: -r["corridor_gap"])[:2]
    to_eval.sort(key=lambda r: -r["corridor_gap"])
    to_eval = to_eval[:a.n_eval]
    rows = []
    for r in to_eval:
        d = make_design(r["plate"], r["aspect"], r["az"], r["pair_gap"],
                        r["corridor_gap"], r["center"], a.floors, r["swap"])
        row = s.evaluate(d, "2x2쌍")
        if row:
            row["corridor_gap"] = r["corridor_gap"]
            row["pair_gap"] = r["pair_gap"]
            row["swap"] = r["swap"]
            rows.append(row)
    if not rows:
        raise SystemExit("일조 평가를 통과한 배치가 없습니다(코드 점검 필요).")
    rows.sort(key=lambda r: r["new_fail"])
    best_overall = min(rows, key=lambda r: r["new_fail"])
    best_wide = max(rows, key=lambda r: r["corridor_gap"])
    print(f"   평가 {len(rows)}개(방위·형 {len(seen)}조합) · "
          f"최대통로안 신규불충족 {best_wide['new_fail']} "
          f"(통로 {best_wide['corridor_gap']:.1f}m) · "
          f"일조최우수 신규불충족 {best_overall['new_fail']} "
          f"(통로 {best_overall['corridor_gap']:.1f}m)")

    # 통로 폭이 가장 넓은 것부터 순서대로, 일조가 그중 최선인 안을 후보로
    # 남긴다 — "통로를 최대한 뽑되, 같은 통로 폭이면 일조가 나은 방위/형을
    # 쓴다"는 원칙을 상위 몇 단계에 걸쳐 반영한다.
    print("\n■ 3단계 — 통로 최대 안 부근 미세조정(방위·짝간격·위치)")
    refine_rows = []
    for seed_row in sorted(rows, key=lambda r: -r["corridor_gap"])[:4]:
        seed = seed_row["_d"]
        t0s = seed.towers[0]
        swap = seed_row["swap"]
        cx = sum(t.cx for t in seed.towers) / 4
        cy = sum(t.cy for t in seed.towers) / 4
        for daz in (-6, -3, 0, 3, 6):
            for pg in PAIR_GAPS:
                az = (t0s.azimuth + daz) % 180
                for dx in (-5.0, 0.0, 5.0):
                    for dy in (-5.0, 0.0, 5.0):
                        g = max_corridor_gap(ctx["envelope"], t0s.plate,
                                             t0s.aspect, az, pg,
                                             (cx + dx, cy + dy), a.floors,
                                             swap)
                        if g is None:
                            continue
                        d = make_design(t0s.plate, t0s.aspect, az, pg, g,
                                        (cx + dx, cy + dy), a.floors, swap)
                        row = s.evaluate(d, "2x2쌍 미세조정")
                        if row:
                            row["corridor_gap"] = g
                            row["pair_gap"] = pg
                            row["swap"] = swap
                            refine_rows.append(row)
    rows += refine_rows
    print(f"   추가 평가 {len(refine_rows)}개")
    best_wide = max(rows, key=lambda r: r["corridor_gap"])
    # '최대한'을 문자 그대로 절대 최댓값 1m 이내로만 잡으면, 통로가 조금만
    # 좁아져도 일조가 크게 나아지는 자리를 놓친다(실제로 그런 자리가
    # 있었다 — 20% 좁혀 신규불충족이 87→75로 줄었다). 최댓값의 80% 이상을
    # '거의 최대'로 보고 그 안에서 일조 최우수를 최종안으로 삼는다.
    near_max = [r for r in rows
               if r["corridor_gap"] >= best_wide["corridor_gap"] * 0.8]
    final = min(near_max, key=lambda r: r["new_fail"])

    # 트레이드오프 표 — 최종안과 같은 방위·짝간격·형에서 통로폭만 바꾼다
    print("\n■ 4단계 — 통로폭 트레이드오프 표")
    fd = final["_d"].towers[0]
    cx = sum(t.cx for t in final["_d"].towers) / 4
    cy = sum(t.cy for t in final["_d"].towers) / 4
    table_rows = []
    gaps = sorted({round(final["corridor_gap"] * k / (a.n_table - 1), 1)
                  for k in range(1, a.n_table)} | {final["corridor_gap"]})
    for g in gaps:
        d = make_design(fd.plate, fd.aspect, fd.azimuth, final["pair_gap"],
                        g, (cx, cy), a.floors, final["swap"])
        row = s.evaluate(d, "2x2쌍 표")
        if row:
            row["corridor_gap"] = g
            row["pair_gap"] = final["pair_gap"]
            row["swap"] = final["swap"]
            table_rows.append(row)
    # 최종안 자체(=이 자리에서의 최대 통로)는 이미 평가돼 s.seen 에 걸려
    # 위 루프에서 조용히 빠진다 — 표 맨 위 칸으로 직접 채워 넣는다.
    if not any(abs(r["corridor_gap"] - final["corridor_gap"]) < 0.5
              for r in table_rows):
        table_rows.append(final)
    table_rows.sort(key=lambda r: r["corridor_gap"])

    M.write_candidates(a.outdir / "candidates.csv", s.rows)
    M.export(a.outdir, final, ctx, a, "최적안")
    M.export(a.outdir, best_wide, ctx, a, "최대통로안")

    L = ["# 화랑 – 20층 4개동 2x2(짝 붙이기 + 가운데 통로) 설계안", "",
        "## 조건", "", "| 항목 | 값 |", "|---|---|",
        "| 배치 | 4개동, 2개씩 짝지어 붙이고(작은 간격) 두 짝 사이는 "
        "최대한 벌려 가운데를 통로로 사용 |",
        f"| 층수·층고 | 20층 · {a.floor_h:g}m (높이 "
        f"{20*a.floor_h+H.ROOFTOP_M:.1f}m) |",
        f"| 연면적 | {ctx['gfa']:,.0f}㎡ · 용적률 {a.far:g}% |",
        "| 동간거리 | 특례 전제 — 절대 하한 "
        f"{a.min_dong_gap:g}m 만 적용 |",
        "", "## 최종안 (통로 최대 부근에서 일조 최우수)", "",
        "| 항목 | 값 |", "|---|---|"]
    for k, v in M.spec_rows(final, a):
        L.append(f"| {k} | {v} |")
    shape_f = "장변끼리(줄을 옆으로 벌림)" if final["swap"] else "단변끼리(판을 길이로 벌림)"
    L += [f"| **가운데 통로 폭** | **{final['corridor_gap']:.1f}m** |",
         f"| 짝 안 간격 | {final['pair_gap']:.0f}m |",
         f"| 통로에서 마주보는 변 | {shape_f} |"]

    L += ["", "## 순수 최대통로안(참고 — 일조 미고려)", "",
         "| 항목 | 값 |", "|---|---|"]
    for k, v in M.spec_rows(best_wide, a):
        L.append(f"| {k} | {v} |")
    shape_w = "장변끼리(줄을 옆으로 벌림)" if best_wide["swap"] else "단변끼리(판을 길이로 벌림)"
    L += [f"| **가운데 통로 폭** | **{best_wide['corridor_gap']:.1f}m** |",
         f"| 짝 안 간격 | {best_wide['pair_gap']:.0f}m |",
         f"| 통로에서 마주보는 변 | {shape_w} |"]

    L += ["", "## 통로 폭 트레이드오프", "",
         f"같은 방위({fd.azimuth:.0f}°)·짝간격({final['pair_gap']:.0f}m)·중심 "
         "위치에서 통로 폭만 바꿨을 때다. **중심을 고정한 채라 단조롭지",
         "않다** — 통로를 넓힐수록 두 쌍이 중심에서 서로 반대 방향으로",
         "벌어지는데, 그 방향에 있는 학교와의 거리가 가까워졌다 멀어졌다",
         "하기 때문이다. 최종안은 이 표와 별개로 방위·짝간격·위치·통로폭을",
         "함께 재탐색해 얻은 값이라 표의 연장선이 아니다 — \"이 자리에",
         "그대로 두고 통로만 바꾸면\"이라는 국소적 민감도 참고표다.", "",
         "| 통로 폭 | 건폐율 | 실면적 | 신규 불충족 | 신규 충족 |",
         "|---:|---:|---:|---:|---:|"]
    for r in table_rows:
        L.append(f"| {r['corridor_gap']:.1f}m | {r['bcr_pct']}% | "
                 f"{r['real_m2']:,}㎡ | {r['new_fail']} | {r['new_ok']} |")

    L += ["", "## 학교·동별 전후 (최종안)", "",
         "| 학교 / 구분 | 수광점 | 기준 충족 | 설계안 충족 | 신규 불충족 | "
         "신규 충족 | 평균 일조 변화 |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for k, n, b, p_, nf, ng, dh in M.school_table(ctx, final):
        L.append(f"| {k} | {n} | {b} | {p_} | {nf} | {ng} | {dh:+.2f}h |")

    L += ["", "## 산출물", "", "| 파일 | 내용 |", "|---|---|",
         "| `최적안_매싱_*.geojson` | 최종안(통로 최대 부근 일조 최우수) |",
         "| `최대통로안_매싱_*.geojson` | 순수 최대통로안(참고) |",
         "| `*_수광점_전체_*.geojson` | 수광점 전후 판정 |",
         "| `candidates.csv` | 평가한 후보 전량 |", "",
         f"기하 탐색 {len(geo):,}개(일조 계산 없음) · 일조 평가 "
         f"{len(s.rows):,}회.", ""]
    (a.outdir / "report.md").write_text("\n".join(L), encoding="utf-8")

    print(f"\n■ 최종안: 통로 {final['corridor_gap']:.1f}m · 짝간격 "
         f"{final['pair_gap']:.0f}m · 신규 불충족 {final['new_fail']} · "
         f"건폐율 {final['bcr_pct']}% · 실면적 {final['real_m2']:,}㎡")
    print(f"→ {a.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
