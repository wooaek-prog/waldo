#!/usr/bin/env python3
"""화랑아파트 – 계단식(스텝) 높이 배분 검토.

확정 매싱(36.97×18.48m, 장변 방위 345°, 55층 균일)을 장변 방향으로 2등분해,
학교 쪽에 가까운 부분은 낮추고 먼 부분은 높이는 "대교아파트식" 계단형 매싱을
검토한다. 두 부분의 충별면적×층수 합이 확정 목표 연면적(용적률 400%,
37,580㎡)과 같아지도록 층수를 맞바꿔가며, 실제 태양궤적 시뮬레이션으로
학교 일조에 가장 유리한 분할점·높이차를 찾는다.

핵심 아이디어
    장변 로컬좌표 +u 방향은 방위각(345°)과 정확히 일치한다(하단 검증 참고).
    이 방향이 학교 30-1(5° 차이)·40-2(14° 차이)·40-1(28° 차이)과 거의
    나란하므로, +u측을 저층으로 낮추고 -u측(방위 165°, 남쪽)을 고층으로
    올리는 배치를 우선 시도한다. 다만 어느 쪽이 실제로 유리한지는
    가정하지 않고 두 방향을 모두 계산해 시뮬레이션으로 검증한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon, mapping

import hwarang_massing_study as H

RESI_FLOOR_H = H.RESI_FLOOR_H
ROOFTOP_M = H.ROOFTOP_M

DEFAULT_CONFIG = Path(__file__).with_name("data") / "hwarang_unit_blocks.json"
DEFAULT_OUTDIR = Path(__file__).with_name("outputs") / "hwarang_stepped"


# --------------------------------------------------------------------------- #
def segment_rect(centre: tuple[float, float], ux: float, uy: float,
                 vx: float, vy: float, u0: float, u1: float, half_w: float) -> Polygon:
    """장변(u) 구간 [u0,u1], 단변 전체 폭(half_w×2)의 실좌표 사각형."""
    corners = []
    for uu, vv in ((u0, -half_w), (u1, -half_w), (u1, half_w), (u0, half_w)):
        corners.append((centre[0] + uu * ux + vv * vx, centre[1] + uu * uy + vv * vy))
    return Polygon(corners)


@dataclass
class Step:
    label: str
    p_end: str          # 어느 쪽(+u/-u)이 저층인지: 'plus_low' 또는 'minus_low'
    split: float         # 분할 비율(저층측이 차지하는 길이 비율, 0~1)
    floors_low: int
    floors_high: int
    area_low: float
    area_high: float
    low_prism: H.Prism
    high_prism: H.Prism

    @property
    def gfa(self) -> float:
        return self.area_low * self.floors_low + self.area_high * self.floors_high


def compute_spans(L: float, p_low_is_plus: bool,
                  split: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """저층부/고층부 각각의 (u0,u1) 구간. split = 저층부가 차지하는 길이 비율(0~1).

    저층부는 지정된 쪽 끝(+u 또는 -u)에서부터 split*L 만큼을 차지한다.
    """
    if p_low_is_plus:
        boundary = L / 2 - split * L
        return (boundary, L / 2), (-L / 2, boundary)
    boundary = -L / 2 + split * L
    return (-L / 2, boundary), (boundary, L / 2)


def build_step(centre, ux, uy, vx, vy, L, half_w, p_low_is_plus: bool,
              split: float, floors_low: int, floors_high: int) -> Step:
    """split = 저층부가 차지하는 길이 비율(0~1). 저층부는 지정된 쪽 끝에 붙인다."""
    low_span, high_span = compute_spans(L, p_low_is_plus, split)
    low_rect = segment_rect(centre, ux, uy, vx, vy, *low_span, half_w)
    high_rect = segment_rect(centre, ux, uy, vx, vy, *high_span, half_w)
    area_low = low_rect.area
    area_high = high_rect.area
    low_prism = H.Prism(low_rect, floors_low * RESI_FLOOR_H + ROOFTOP_M,
                        f"저층부({floors_low}F)")
    high_prism = H.Prism(high_rect, floors_high * RESI_FLOOR_H + ROOFTOP_M,
                         f"고층부({floors_high}F)")
    side = "+u(345°)측 저층" if p_low_is_plus else "-u(165°)측 저층"
    return Step(f"분할{split:.2f}·{side}·{floors_low}F/{floors_high}F", side, split,
               floors_low, floors_high, area_low, area_high, low_prism, high_prism)


def floors_high_for_target(area_low: float, floors_low: int, area_high: float,
                           target_gfa: float) -> int:
    remaining = target_gfa - area_low * floors_low
    if remaining <= 0 or area_high <= 0:
        return 0
    return max(1, round(remaining / area_high))


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="화랑아파트 계단식 높이 배분 검토")
    parser.add_argument("--buildings", type=Path, required=True)
    parser.add_argument("--daegyo", type=Path,
                        default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="확정 매싱 제원(plate)을 읽어올 설정파일")
    parser.add_argument("--site-area", type=float, default=9395.0)
    parser.add_argument("--far", type=float, default=400.0)
    parser.add_argument("--bcr", type=float, default=60.0)
    parser.add_argument("--floors", type=int, default=55, help="현재 균일 매싱 층수(비교 기준)")
    parser.add_argument("--date", type=str, default="12-22")
    parser.add_argument("--school-radius", type=float, default=350.0)
    parser.add_argument("--step-min", type=int, default=10)
    parser.add_argument("--min-floors", type=int, default=20, help="저층부 최소 층수")
    parser.add_argument("--max-floors", type=int, default=75, help="고층부 최대 층수(구조 상한 참고용)")
    parser.add_argument("--top-n", type=int, default=10)
    return parser.parse_args(argv)


def evaluate_step(receptors, context, times, masks, step_min, step: Step):
    results = H.evaluate(receptors, [step.low_prism, step.high_prism], times, masks, step_min)
    return H.summarize(results), H.per_school(results)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    plate = cfg["plate"]

    ctx = H.prepare_analysis(args)
    receptors, times = ctx["receptors"], ctx["times"]
    context, existing = ctx["context"], ctx["existing_hwarang"]

    L = plate["long_mm"] / 1000.0
    W = plate["short_mm"] / 1000.0
    az = plate["long_azimuth_deg"]
    centre = tuple(plate["centre_epsg5186"])
    ang = math.radians(az)
    ux, uy = math.sin(ang), math.cos(ang)
    vx, vy = math.sin(ang + math.pi / 2), math.cos(ang + math.pi / 2)
    half_w = W / 2.0
    target_gfa = args.site_area * args.far / 100.0

    print(f"확정 매싱: {L:.2f}×{W:.2f}m(={L*W:,.1f}㎡), 장변 방위 {az:g}°, "
          f"+u측 끝 방위 {az:g}°, -u측 끝 방위 {(az+180)%360:g}°")
    print(f"목표 연면적(400%): {target_gfa:,.0f}㎡\n")

    masks = H.build_context_masks(context, receptors, times)

    # 균일(현재 확정) 55층 기준선
    uniform_rect = segment_rect(centre, ux, uy, vx, vy, -L / 2, L / 2, half_w)
    uniform_prism = H.Prism(uniform_rect, args.floors * RESI_FLOOR_H + ROOFTOP_M, "균일55F")
    uniform_res = H.evaluate(receptors, [uniform_prism], times, masks, args.step_min)
    uniform_stats, uniform_school = H.summarize(uniform_res), H.per_school(uniform_res)
    now_stats = H.summarize(H.evaluate(receptors, existing, times, masks, args.step_min))
    now_school = H.per_school(H.evaluate(receptors, existing, times, masks, args.step_min))

    print(f"기준선: 현황 10층 3개동 {now_stats['pass_pct']:.1f}% / "
          f"확정 균일 {args.floors}층 {uniform_stats['pass_pct']:.1f}%\n")

    # ── 1단계: 굵은 격자 탐색 (양방향 × 분할비 × 저층수) ───────────────────
    coarse_results: list[dict[str, Any]] = []
    for p_low_is_plus in (True, False):
        for split in (0.05, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
            for floors_low in range(args.min_floors, args.floors, 5):
                low_span, high_span = compute_spans(L, p_low_is_plus, split)
                area_low = (low_span[1] - low_span[0]) * W
                area_high = (high_span[1] - high_span[0]) * W
                floors_high = floors_high_for_target(area_low, floors_low, area_high, target_gfa)
                if not (args.min_floors <= floors_high <= args.max_floors):
                    continue
                step = build_step(centre, ux, uy, vx, vy, L, half_w, p_low_is_plus,
                                  split, floors_low, floors_high)
                stats, school = evaluate_step(receptors, context, times, masks,
                                              args.step_min * 2, step)  # 굵은 격자: 시간해상도 완화
                coarse_results.append({"step": step, "stats": stats, "school": school})

    coarse_results.sort(key=lambda r: (
        -round(r["stats"]["pass_pct"] * 2) / 2,
        -min(r["school"][j] - now_school.get(j, 0.0) for j in r["school"]),
    ))
    print(f"1단계 굵은 탐색: {len(coarse_results)}개 후보 평가\n")
    for rank, row in enumerate(coarse_results[:5], 1):
        s, st = row["step"], row["stats"]
        print(f"   {rank}위 {s.label:<40} 기준A {st['pass_pct']:.1f}%")

    # ── 2단계: 상위 후보 주변 정밀 재탐색(원 해상도) ───────────────────────
    seeds = coarse_results[:6]
    fine_results: list[dict[str, Any]] = []
    seen: set[tuple[bool, float, int]] = set()
    for seed in seeds:
        s = seed["step"]
        p_low_is_plus = (s.p_end == "+u(345°)측 저층")
        for split in (s.split - 0.05, s.split, s.split + 0.05):
            if not (0.05 <= split <= 0.95):
                continue
            for floors_low in range(max(args.min_floors, s.floors_low - 3),
                                    min(args.floors, s.floors_low + 4)):
                key = (p_low_is_plus, round(split, 3), floors_low)
                if key in seen:
                    continue
                seen.add(key)
                low_span, high_span = compute_spans(L, p_low_is_plus, split)
                area_low = (low_span[1] - low_span[0]) * W
                area_high = (high_span[1] - high_span[0]) * W
                floors_high = floors_high_for_target(area_low, floors_low, area_high, target_gfa)
                if not (args.min_floors <= floors_high <= args.max_floors):
                    continue
                step = build_step(centre, ux, uy, vx, vy, L, half_w, p_low_is_plus,
                                  split, floors_low, floors_high)
                stats, school = evaluate_step(receptors, context, times, masks,
                                              args.step_min, step)
                fine_results.append({"step": step, "stats": stats, "school": school})

    fine_results.sort(key=lambda r: (
        -round(r["stats"]["pass_pct"] * 2) / 2,
        -min(r["school"][j] - now_school.get(j, 0.0) for j in r["school"]),
        -r["stats"]["pass_pct"],
    ))
    print(f"\n2단계 정밀평가: {len(fine_results)}개 후보")
    print(f"\n{'순위':>3} {'구성':<42}{'기준A':>7}{'현황대비':>9}{'학교최악':>9}{'저층부㎡':>9}{'고층부㎡':>9}")
    print("-" * 90)
    for rank, row in enumerate(fine_results[:args.top_n], 1):
        s, st, sc = row["step"], row["stats"], row["school"]
        delta_all = st["pass_pct"] - uniform_stats["pass_pct"]
        delta_min = min(sc[j] - now_school.get(j, 0.0) for j in sc)
        print(f"{rank:>3} {s.label:<42}{st['pass_pct']:>6.1f}%{delta_all:>+8.1f}%p"
              f"{delta_min:>+8.1f}%p{s.area_low:>9.1f}{s.area_high:>9.1f}")

    best = fine_results[0]
    bs, bst, bsc = best["step"], best["stats"], best["school"]
    print(f"\n■ 균일 55층 대비 비교")
    print(f"   균일 55층 : 기준A {uniform_stats['pass_pct']:.1f}%")
    print(f"   계단형 최적: 기준A {bst['pass_pct']:.1f}% "
          f"({bst['pass_pct']-uniform_stats['pass_pct']:+.1f}%p)")
    print(f"\n■ 학교별 비교 (현황 대비)")
    for j in sorted(bsc):
        print(f"   {j}: 현황 {now_school[j]:5.1f}% → 균일55F {uniform_school[j]:5.1f}% "
              f"→ 계단형 {bsc[j]:5.1f}%  (균일 대비 {bsc[j]-uniform_school[j]:+5.1f}%p)")

    print(f"\n■ 권장 계단형 매싱: {bs.label}")
    print(f"   저층부: {bs.floors_low}층, 면적 {bs.area_low:.1f}㎡, "
          f"높이 {bs.low_prism.top_m:.1f}m")
    print(f"   고층부: {bs.floors_high}층, 면적 {bs.area_high:.1f}㎡, "
          f"높이 {bs.high_prism.top_m:.1f}m")
    print(f"   달성 연면적 {bs.gfa:,.0f}㎡ (목표 {target_gfa:,.0f}㎡, "
          f"편차 {(bs.gfa-target_gfa)/target_gfa*100:+.2f}%)")

    write_outputs(args, plate, uniform_prism, uniform_stats, now_stats, now_school,
                 uniform_school, fine_results, context, ctx["schools"], target_gfa)
    return 0


def write_outputs(args, plate, uniform_prism, uniform_stats, now_stats, now_school,
                  uniform_school, fine_results, context, schools, target_gfa) -> None:
    def dump(path: Path, features, epsg: int = 5186) -> None:
        payload = {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}},
            "features": [{"type": "Feature", "geometry": mapping(g), "properties": p}
                         for g, p in features],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    top = fine_results[: args.top_n]
    best = top[0]["step"]
    dump(args.outdir / "hwarang_stepped_best.geojson", [
        (best.low_prism.footprint, {
            "part": "저층부", "floors": best.floors_low,
            "height_m": round(best.low_prism.top_m, 2),
            "base_elev_m": H.GROUND_ELEV_M,
            "top_elev_m": round(H.GROUND_ELEV_M + best.low_prism.top_m, 2),
            "area_m2": round(best.area_low, 1),
        }),
        (best.high_prism.footprint, {
            "part": "고층부", "floors": best.floors_high,
            "height_m": round(best.high_prism.top_m, 2),
            "base_elev_m": H.GROUND_ELEV_M,
            "top_elev_m": round(H.GROUND_ELEV_M + best.high_prism.top_m, 2),
            "area_m2": round(best.area_high, 1),
        }),
    ])
    dump(args.outdir / "hwarang_stepped_uniform_baseline.geojson", [
        (uniform_prism.footprint, {
            "part": "균일(비교기준)", "floors": args.floors,
            "height_m": round(uniform_prism.top_m, 2),
            "top_elev_m": round(H.GROUND_ELEV_M + uniform_prism.top_m, 2),
        }),
    ])

    with (args.outdir / "hwarang_stepped_comparison.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["순위", "구성", "분할비", "저층방향", "저층수", "고층수",
                         "저층면적㎡", "고층면적㎡", "달성연면적㎡", "연면적편차%",
                         "기준A%", "균일대비%p", "학교최악%p"]
                        + [f"학교{j}%" for j in sorted(schools[0].keys()) if False]
                        + sorted({j for row in fine_results for j in row["school"]}))
        for rank, row in enumerate(fine_results, 1):
            s, st, sc = row["step"], row["stats"], row["school"]
            delta_all = st["pass_pct"] - uniform_stats["pass_pct"]
            delta_min = min(sc[j] - now_school.get(j, 0.0) for j in sc)
            writer.writerow([
                rank, s.label, round(s.split, 2), s.p_end, s.floors_low, s.floors_high,
                round(s.area_low, 1), round(s.area_high, 1), round(s.gfa, 0),
                round((s.gfa - target_gfa) / target_gfa * 100, 2),
                round(st["pass_pct"], 1), round(delta_all, 1), round(delta_min, 1),
            ] + [round(sc[j], 1) for j in sorted(sc)])

    export_step_svg(args.outdir / "hwarang_stepped_elevation.svg", plate, best, uniform_prism, args.floors)
    print(f"\n결과 저장: {args.outdir}")


def export_step_svg(path: Path, plate, best, uniform_prism, uniform_floors) -> None:
    """장변 방향 입면도(측면 실루엣) 비교 – 균일안 vs 계단형안."""
    L = plate["long_mm"] / 1000.0
    scale_x = 900.0 / L
    max_h = max(uniform_prism.top_m, best.low_prism.top_m, best.high_prism.top_m) * 1.05
    scale_y = 340.0 / max_h

    def bar(u0, u1, h, colour, label):
        x0, x1 = (u0 + L / 2) * scale_x, (u1 + L / 2) * scale_x
        y = 360.0 - h * scale_y
        return (f'<rect x="{x0:.1f}" y="{y:.1f}" width="{x1-x0:.1f}" '
               f'height="{h*scale_y:.1f}" fill="{colour}" fill-opacity="0.85" '
               f'stroke="#111" stroke-width="1.2"/>'
               f'<text x="{(x0+x1)/2:.1f}" y="{y-6:.1f}" font-family="sans-serif" '
               f'font-size="12" text-anchor="middle" fill="#111">{label}</text>')

    is_plus_low = best.p_end.startswith("+u")
    u_split = -L / 2 + best.split * L
    if is_plus_low:
        low_span, high_span = (u_split, L / 2), (-L / 2, u_split)
    else:
        low_span, high_span = (-L / 2, u_split), (u_split, L / 2)

    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="960" height="430" '
          'viewBox="0 0 960 430">',
          '<rect width="100%" height="100%" fill="#fbfbf9"/>',
          '<text x="12" y="22" font-family="sans-serif" font-size="16" font-weight="bold" '
          'fill="#111">화랑아파트 계단형 매싱 – 장변 입면 비교 '
          f'(좌측 =방위{(plate["long_azimuth_deg"]+180)%360:g}°/남측, '
          f'우측=방위{plate["long_azimuth_deg"]:g}°/학교측)</text>']
    out.append('<g transform="translate(20,30)">')
    out.append(f'<text x="450" y="16" font-family="sans-serif" font-size="13" '
              f'fill="#555" text-anchor="middle">① 현재 확정안(균일 {uniform_floors}층)</text>')
    out.append(bar(-L / 2, L / 2, uniform_prism.top_m, "#8d99ae",
                  f"{uniform_floors}F"))
    out.append("</g>")
    out.append('<g transform="translate(20,220)">')
    out.append(f'<text x="450" y="16" font-family="sans-serif" font-size="13" '
              f'fill="#555" text-anchor="middle">② 계단형 대안</text>')
    out.append(bar(*high_span, best.high_prism.top_m, "#1d3557", f"{best.floors_high}F"))
    out.append(bar(*low_span, best.low_prism.top_m, "#e63946", f"{best.floors_low}F(학교측 저감)"
                  if is_plus_low else f"{best.floors_low}F"))
    out.append("</g>")
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
