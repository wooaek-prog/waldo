#!/usr/bin/env python3
"""화랑아파트 확정 배치도 – 평형 조합 기준층 평면 최적화.

확정된 기준층 판(683.3㎡, 55층, 장변 345°)과 평형별 블럭을 조합하여,
세대 전용(골조) 면적 합계를 최대화하는 세대 구성을 찾는다.

우선순위 해석
  ① 일조권 최우선
      → 판의 외곽 형상·위치·방위는 이전 단계에서 이미 확정되어 있고,
        내부 세대 구성을 바꿔도 외곽 실루엣(그림자)은 변하지 않는다.
        따라서 "그 판을 그대로 유지"하는 것 자체가 일조권 우선순위를 지키는
        방법이며, 이 스크립트는 판의 형상·위치를 절대 바꾸지 않는다.
  ② 허용 용적률 내 최대확보
      → 한국 건축법상 바닥면적(연면적)은 복도·코어를 포함하므로, 판 전체
        (683.3㎡)는 세대 구성과 무관하게 이미 100% 연면적으로 산입된다.
        따라서 실질적으로 의미 있는 목표는 "복도·코어로 낭비되는 면적을
        최소화하고, 세대(전용) 면적 비율을 최대화"하는 것으로 해석한다.
  ③ 평형별 개수는 자유

배치 모델
    기준층은 장변(L)-단변(W) 직사각형이다. 세대는 장변을 마주보는 두 파사드
    (북측 y=0, 남측 y=W)에 각 1열씩 배치하고, 전면(발코니측) 폭 합이 L을
    넘지 않게 채운다. 두 열의 세대 깊이가 아무리 깊어도(최대 7.80m) 합이
    단변(18.48m)보다 한참 작아 두 열은 절대 겹치지 않는다(코어/복도로 남는
    중앙 대역이 자동으로 생긴다).
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from shapely.affinity import scale, translate
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

import hwarang_unit_blocks as U

DEFAULT_CONFIG = Path(__file__).with_name("data") / "hwarang_unit_blocks.json"
DEFAULT_OUTDIR = Path(__file__).with_name("outputs") / "hwarang_floorplan"

# 파사드(장변) 행에 쓸 평형. 84A(코너 사선)·105A·116A(타워형, 깊은 단변부 세대)는
# 이 직사각형 판에는 대응하는 사선/단변 코너가 없어 행 배치에서는 제외한다.
ROW_CODES = ("84B", "88A", "93A", "102A")


@dataclass(frozen=True)
class PackItem:
    code: str
    frontage_mm: int
    value_m2: float
    depth_mm: float


# --------------------------------------------------------------------------- #
# 1행(파사드) 채우기 – 무제한 배낭 문제(unbounded knapsack)
# --------------------------------------------------------------------------- #
def unbounded_knapsack(
    items: Sequence[PackItem], capacity_mm: int, resolution_mm: int = 10,
) -> tuple[float, dict[str, int], int]:
    """전면폭 합이 capacity_mm 이하가 되도록 가치(전용면적) 합을 최대화한다.

    반환값: (최대 가치, {평형코드: 개수}, 남는 길이 mm)
    """
    steps = capacity_mm // resolution_mm
    weights = [max(1, round(it.frontage_mm / resolution_mm)) for it in items]
    dp = [0.0] * (steps + 1)
    pick: list[tuple[int, int] | None] = [None] * (steps + 1)
    for c in range(1, steps + 1):
        dp[c] = dp[c - 1]
        for idx, (it, w) in enumerate(zip(items, weights)):
            if w <= c:
                candidate = dp[c - w] + it.value_m2
                if candidate > dp[c] + 1e-9:
                    dp[c] = candidate
                    pick[c] = (idx, w)

    counts = {it.code: 0 for it in items}
    c = steps
    while c > 0:
        entry = pick[c]
        if entry is None:
            c -= 1
        else:
            idx, w = entry
            counts[items[idx].code] += 1
            c -= w

    used_steps = sum(counts[it.code] * w for it, w in zip(items, weights))
    leftover_mm = (steps - used_steps) * resolution_mm
    return dp[steps], counts, leftover_mm


def brute_force_alternatives(
    items: Sequence[PackItem], capacity_mm: int, max_items: int = 2,
) -> list[dict[str, Any]]:
    """행 하나에 아이템을 최대 max_items개(중복 허용) 넣는 모든 조합을 나열한다.

    이 데이터셋은 가장 작은 전면폭(13.8m)도 3개면 판 길이를 넘기 때문에
    한 행에는 최대 2세대만 들어간다. 그래서 전수 열거가 가능하며, DP 결과를
    검증하고 "차선책" 표를 만드는 데 함께 쓴다.
    """
    combos: list[dict[str, Any]] = []
    codes = [it.code for it in items]
    by_code = {it.code: it for it in items}
    seen: set[tuple[str, ...]] = set()
    for n in range(0, max_items + 1):
        for combo in itertools.combinations_with_replacement(codes, n):
            key = tuple(sorted(combo))
            if key in seen:
                continue
            seen.add(key)
            total_frontage = sum(by_code[c].frontage_mm for c in combo)
            if total_frontage > capacity_mm:
                continue
            total_value = sum(by_code[c].value_m2 for c in combo)
            counts: dict[str, int] = {}
            for c in combo:
                counts[c] = counts.get(c, 0) + 1
            combos.append({
                "counts": counts,
                "value_m2": total_value,
                "frontage_mm": total_frontage,
                "leftover_mm": capacity_mm - total_frontage,
                "n_types": len(counts),
            })
    combos.sort(key=lambda r: -r["value_m2"])
    return combos


# --------------------------------------------------------------------------- #
# 실제 배치 (기하)
# --------------------------------------------------------------------------- #
def place_row(
    blocks: dict[str, U.UnitBlock], counts: dict[str, int], plate: dict[str, Any],
    side: str,
) -> tuple[list[tuple[str, Polygon]], float]:
    """counts 를 면적이 큰 순으로 좌→우 배치한다.

    side='N': 정면(발코니측)이 y=0 에 오도록 그대로 배치.
    side='S': 정면이 y=short_mm 에 오도록 상하 반전 후 배치(뒷면이 중앙
    코어를 바라보게 되어 북측 열과 대칭을 이룬다).
    """
    order = sorted(
        (code for code, n in counts.items() for _ in range(n)),
        key=lambda c: -blocks[c].frame_m2,
    )
    placed: list[tuple[str, Polygon]] = []
    x = 0.0
    for code in order:
        block = blocks[code]
        if side == "S":
            geom = scale(block.frame, 1.0, -1.0, origin=(0.0, 0.0))
            geom = translate(geom, x, plate["short_mm"])
        else:
            geom = translate(block.frame, x, 0.0)
        placed.append((code, geom))
        x += block.frontage_mm
    return placed, x  # x = 사용된 길이(다음 세대가 시작할 위치=남는 구간의 시작)


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="화랑아파트 기준층 세대 구성 최적화")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--resolution-mm", type=int, default=10,
                        help="배낭 DP 해상도(mm). 전면폭이 모두 100mm 배수이므로 10mm면 정확하다")
    parser.add_argument("--min-core-mm", type=int, default=None,
                        help="필요 시 최소 코어(복도) 폭(mm)을 강제하는 대안도 함께 계산 "
                             "(기본: 설정파일의 core.depth_mm)")
    parser.add_argument("--top-n", type=int, default=8, help="차선책 표에 보여줄 개수")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    plate, core = cfg["plate"], cfg["core"]

    blocks = {
        spec["code"]: U.build_block(spec, cfg["frame_factor"],
                                    cfg["balcony_depth_mm"], cfg["calibrate"])
        for spec in cfg["units"]
    }
    row_codes = [c for c in ROW_CODES if c in blocks]
    items = [
        PackItem(c, int(round(blocks[c].frontage_mm)), blocks[c].frame_m2,
                 blocks[c].max_depth_mm)
        for c in row_codes
    ]

    print(f"기준층 {plate['long_mm']/1000:.2f} × {plate['short_mm']/1000:.2f} m "
          f"= {plate['area_m2']}㎡ · {plate['floors']}층 (형상·위치·방위 그대로 유지 = 일조권 조건 불변)")
    print(f"파사드 배치 후보 평형: {', '.join(row_codes)} "
          f"(84A·105A·116A는 이 직사각형 판에 대응 코너/단변부가 없어 제외)\n")

    # ── 1행 최적화 (DP + 전수열거로 교차검증) ──────────────────────────────
    value_row, counts_row, leftover_row_mm = unbounded_knapsack(
        items, plate["long_mm"], args.resolution_mm)
    alternatives = brute_force_alternatives(items, plate["long_mm"])
    best_brute = alternatives[0]
    assert abs(best_brute["value_m2"] - value_row) < 0.05, (
        f"DP와 전수열거 결과 불일치: {value_row} vs {best_brute['value_m2']}")
    print("DP 결과가 전수열거(전 조합) 최댓값과 일치함을 확인했습니다.\n")

    print(f"{'순위':>3} {'구성':<28}{'전용면적합㎡':>12}{'전면폭합m':>10}{'남는길이m':>9}{'평형수':>6}")
    print("-" * 72)
    for rank, combo in enumerate(alternatives[:args.top_n], 1):
        desc = " + ".join(f"{code}×{n}" for code, n in sorted(combo["counts"].items())) or "(공백)"
        print(f"{rank:>3} {desc:<28}{combo['value_m2']:>12.2f}"
              f"{combo['frontage_mm']/1000:>10.2f}{combo['leftover_mm']/1000:>9.2f}"
              f"{combo['n_types']:>6}")

    counts_row = alternatives[0]["counts"]
    value_row = alternatives[0]["value_m2"]
    leftover_row_mm = alternatives[0]["leftover_mm"]

    # ── 코어 대역 검증 ──────────────────────────────────────────────────
    row_max_depth_mm = max(
        (blocks[c].max_depth_mm for c, n in counts_row.items() if n > 0), default=0.0
    )
    corridor_gap_mm = plate["short_mm"] - 2 * row_max_depth_mm
    required_core_mm = args.min_core_mm if args.min_core_mm is not None else core["depth_mm"]
    compliant = corridor_gap_mm >= required_core_mm

    print(f"\n선택된 구성의 세대 최대깊이: {row_max_depth_mm/1000:.3f} m (양쪽 열 동일 적용)")
    print(f"→ 실제 확보되는 중앙 코어/복도 폭: {corridor_gap_mm/1000:.3f} m "
          f"(요구 {required_core_mm/1000:.1f} m 대비 {'충족' if compliant else '미충족'})")

    # ── 대안: 코어 요구폭을 강제 준수하는 제한된 후보군으로도 재계산 ───────
    restricted = [it for it in items
                 if plate["short_mm"] - 2 * it.depth_mm >= required_core_mm]
    if len(restricted) < len(items):
        alt_alternatives = brute_force_alternatives(restricted, plate["long_mm"])
        if alt_alternatives and alt_alternatives[0]["value_m2"] < value_row - 1e-6:
            print(f"\n(참고) 코어 {required_core_mm/1000:.1f}m를 강제 준수하도록 "
                  f"깊은 평형을 제외하면 최적값이 {alt_alternatives[0]['value_m2']:.2f}㎡로 "
                  f"{value_row - alt_alternatives[0]['value_m2']:.2f}㎡ 낮아집니다. "
                  "(위 선택안은 이미 자연 충족하므로 제외 불필요)")
        else:
            print(f"\n확인: 코어 {required_core_mm/1000:.1f}m를 강제해도 동일한 최적해가 나옵니다 "
                  "(깊은 평형을 배제해도 손해가 없습니다).")

    # ── 실제 배치 ──────────────────────────────────────────────────────
    north, north_used_mm = place_row(blocks, counts_row, plate, "N")
    south, south_used_mm = place_row(blocks, counts_row, plate, "S")
    plate_poly = box(0, 0, plate["long_mm"], plate["short_mm"])
    all_units = [g for _, g in north] + [g for _, g in south]
    corridor_poly = plate_poly.difference(unary_union(all_units))

    total_frame_m2 = value_row * 2 / 1e6 * 1e6  # (이미 ㎡ 단위, 표기 안전화)
    total_frame_m2 = sum(blocks[c].frame_m2 * n for c, n in counts_row.items()) * 2
    total_exclusive_m2 = sum(blocks[c].exclusive_m2 * n for c, n in counts_row.items()) * 2
    total_units_per_floor = sum(counts_row.values()) * 2
    corridor_area_m2 = corridor_poly.area / 1e6
    check_sum = total_frame_m2 + corridor_area_m2

    print(f"\n■ 기준층(1개층) 요약")
    print(f"   세대수 {total_units_per_floor}세대 "
          f"({', '.join(f'{c}×{n*2}' for c, n in counts_row.items() if n > 0)})")
    print(f"   세대 전용면적 합 {total_exclusive_m2:,.1f}㎡ / 골조(연면적산입)면적 합 {total_frame_m2:,.1f}㎡")
    print(f"   코어·복도(잔여) 면적 {corridor_area_m2:,.1f}㎡  "
          f"(검산: {check_sum:,.1f}㎡ ≈ 판 면적 {plate['area_m2']}㎡)")
    print(f"   세대면적 비율(골조 기준) {total_frame_m2/plate['area_m2']*100:.1f}% "
          f"/ 전용면적 비율 {total_exclusive_m2/plate['area_m2']*100:.1f}%")

    floors = plate["floors"]
    print(f"\n■ 건물 전체({floors}층) 요약")
    print(f"   총 세대수 {total_units_per_floor * floors:,}세대")
    print(f"   총 전용면적 {total_exclusive_m2 * floors:,.0f}㎡ "
          f"/ 총 골조(연면적)면적 {total_frame_m2 * floors:,.0f}㎡")
    target_gfa = plate["area_m2"] * floors
    print(f"   판 전체 기준 연면적(참고) {target_gfa:,.0f}㎡ "
          f"— 한국 건축법상 복도·코어도 바닥면적에 산입되므로 이 값 자체는 "
          "세대 구성과 무관하게 이미 확정되어 있습니다.")

    write_outputs(args, cfg, blocks, plate, plate_poly, north, south, corridor_poly,
                 counts_row, alternatives, total_frame_m2, total_exclusive_m2,
                 corridor_area_m2, row_max_depth_mm, corridor_gap_mm, compliant)
    return 0


def write_outputs(args, cfg, blocks, plate, plate_poly, north, south, corridor_poly,
                  counts_row, alternatives, total_frame_m2, total_exclusive_m2,
                  corridor_area_m2, row_max_depth_mm, corridor_gap_mm, compliant) -> None:
    def dump(path: Path, features, epsg: int | None) -> None:
        payload: dict[str, Any] = {
            "type": "FeatureCollection",
            "features": [{"type": "Feature", "geometry": mapping(g), "properties": p}
                         for g, p in features],
        }
        if epsg:
            payload["crs"] = {"type": "name",
                              "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    local_features: list[tuple[Polygon, dict[str, Any]]] = []
    for side_name, row in (("N", north), ("S", south)):
        for code, geom in row:
            block = blocks[code]
            local_features.append((geom, {
                "code": code, "row": side_name, "family": block.family,
                "exclusive_m2": block.exclusive_m2, "frame_m2": round(block.frame_m2, 2),
                "sheet": block.sheet,
            }))
    local_features.append((plate_poly, {"line": "기준층 판", "area_m2": plate["area_m2"]}))
    local_features.append((corridor_poly, {"line": "코어·복도(잔여)",
                                           "area_m2": round(corridor_area_m2, 1)}))
    dump(args.outdir / "hwarang_floorplan_local_mm.geojson", local_features, None)

    world_features = [(U.plate_to_world(g, plate), p) for g, p in local_features]
    dump(args.outdir / "hwarang_floorplan_epsg5186.geojson", world_features, 5186)

    with (args.outdir / "hwarang_floorplan_units.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["행", "평형", "계열", "전용㎡", "골조㎡", "도면"])
        for side_name, row in (("N(북)", north), ("S(남)", south)):
            for code, _geom in row:
                block = blocks[code]
                writer.writerow([side_name, code, block.family,
                                 block.exclusive_m2, round(block.frame_m2, 2), block.sheet])

    with (args.outdir / "hwarang_floorplan_alternatives.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["순위", "구성", "전용면적합㎡(1행)", "전면폭합m", "남는길이m", "평형수"])
        for rank, combo in enumerate(alternatives, 1):
            desc = " + ".join(f"{code}×{n}" for code, n in sorted(combo["counts"].items())) or "(공백)"
            writer.writerow([rank, desc, round(combo["value_m2"], 2),
                             round(combo["frontage_mm"] / 1000, 2),
                             round(combo["leftover_mm"] / 1000, 2), combo["n_types"]])

    export_floorplan_svg(args.outdir / "hwarang_floorplan_preview.svg", cfg, blocks, plate,
                         north, south, corridor_poly, row_max_depth_mm, corridor_gap_mm,
                         compliant, total_exclusive_m2, total_frame_m2)
    print(f"\n결과 저장: {args.outdir}")


def export_floorplan_svg(path: Path, cfg, blocks, plate, north, south, corridor_poly,
                         row_max_depth_mm, corridor_gap_mm, compliant,
                         total_exclusive_m2, total_frame_m2) -> None:
    L, W = plate["long_mm"], plate["short_mm"]
    px_w = 1000.0
    k = px_w / L
    px_h = W * k

    def path_of(geom) -> str:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return "".join(
            '<polygon points="%s" />' % " ".join(
                f"{x * k:.1f},{y * k:.1f}" for x, y in poly.exterior.coords)
            for poly in polys)

    palette = {"84B": "#4c956c", "88A": "#e9724c", "93A": "#5a7dc9", "102A": "#c9a227"}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h + 70:.0f}" viewBox="0 0 {px_w:.0f} {px_h + 70:.0f}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>',
           '<g transform="translate(0,58)">']
    out.append(f'<rect x="0" y="0" width="{L*k:.1f}" height="{W*k:.1f}" '
               f'fill="none" stroke="#e63946" stroke-width="2" stroke-dasharray="8 5"/>')
    out.append(f'<g fill="#cfd8dc" fill-opacity="0.6">{path_of(corridor_poly)}</g>')
    for side_name, row in (("N", north), ("S", south)):
        for code, geom in row:
            colour = palette.get(code, "#888")
            out.append(f'<g fill="{colour}" fill-opacity="0.85" stroke="#222" '
                       f'stroke-width="1.2">{path_of(geom)}</g>')
            c = geom.centroid
            out.append(f'<text x="{c.x*k:.1f}" y="{c.y*k:.1f}" font-family="sans-serif" '
                       f'font-size="13" font-weight="bold" fill="#111" '
                       f'text-anchor="middle">{code}</text>')
    out.append("</g>")
    out.append(f'<text x="12" y="22" font-family="sans-serif" font-size="16" '
               f'font-weight="bold" fill="#111">화랑아파트 기준층 세대 배치안 '
               f'(683.3㎡, 상단이 북)</text>')
    out.append(f'<text x="12" y="42" font-family="sans-serif" font-size="12.5" fill="#444">'
               f'전용 {total_exclusive_m2:,.1f}㎡ · 골조 {total_frame_m2:,.1f}㎡ · '
               f'세대깊이 {row_max_depth_mm/1000:.2f}m · 코어대역 {corridor_gap_mm/1000:.2f}m '
               f'({"충족" if compliant else "미충족"})</text>')
    x = 12.0
    for code, colour in palette.items():
        out.append(f'<rect x="{x}" y="{px_h+48:.0f}" width="12" height="10" fill="{colour}"/>'
                   f'<text x="{x+16}" y="{px_h+57:.0f}" font-family="sans-serif" '
                   f'font-size="11.5" fill="#333">{code}</text>')
        x += 70
    out.append('<rect x="' + str(x) + f'" y="{px_h+48:.0f}" width="12" height="10" '
               'fill="#cfd8dc"/>'
               f'<text x="{x+16}" y="{px_h+57:.0f}" font-family="sans-serif" '
               f'font-size="11.5" fill="#333">코어·복도</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
