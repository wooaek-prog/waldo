#!/usr/bin/env python3
"""화랑아파트 재건축 1개동 – 최적 평면 형상 도출.

기준층 면적(=용적률)과 층수를 고정한 채 평면 형상만 바꾸어,
주변 학교 교실 창면의 동지일 일조에 미치는 영향을 비교한다.

산출 우선순위
  ① 현황(10층 3개동) 대비 일조 개선
  ② 최대한 직사각형에 가까운 형상
  ③ 직사각형이 어려우면 '<'(V) 등 꺾인 형상 검토
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from shapely.affinity import rotate, scale, translate
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

import hwarang_massing_study as H

RESI_FLOOR_H = H.RESI_FLOOR_H
ROOFTOP_M = H.ROOFTOP_M


# --------------------------------------------------------------------------- #
# 형상 라이브러리 (원점 중심, 이후 목표 면적으로 정규화)
# --------------------------------------------------------------------------- #
def shape_rect(aspect: float) -> Polygon:
    """직사각형 – aspect = 장변/단변."""
    return box(-aspect / 2, -0.5, aspect / 2, 0.5)


def shape_octagon(cut: float, aspect: float = 1.0) -> Polygon:
    """모서리를 잘라낸 팔각형(실무에서 흔한 탑상형 기준층)."""
    a, b = aspect / 2, 0.5
    c = cut * min(a, b)
    return Polygon([
        (-a + c, -b), (a - c, -b), (a, -b + c), (a, b - c),
        (a - c, b), (-a + c, b), (-a, b - c), (-a, -b + c),
    ])


def shape_ellipse(aspect: float, segments: int = 48) -> Polygon:
    return Polygon([
        (aspect / 2 * math.cos(2 * math.pi * i / segments),
         0.5 * math.sin(2 * math.pi * i / segments))
        for i in range(segments)
    ])


def _arm(length: float, width: float, angle_deg: float) -> Polygon:
    """원점에서 angle_deg 방향으로 뻗는 폭 width 의 팔."""
    bar = box(0.0, -width / 2, length, width / 2)
    return rotate(bar, angle_deg, origin=(0.0, 0.0))


def shape_v(open_deg: float, width: float = 0.34, arm: float = 1.0) -> Polygon:
    """'<' 형상 – 두 팔이 open_deg 로 벌어진 꺾인 판."""
    half = open_deg / 2.0
    return unary_union([_arm(arm, width, half), _arm(arm, width, -half)]).buffer(0)


def shape_l(width: float = 0.34, arm: float = 1.0) -> Polygon:
    """'ㄱ' 형상 – 직각으로 꺾인 판."""
    return unary_union([_arm(arm, width, 0.0), _arm(arm, width, 90.0)]).buffer(0)


def shape_t(width: float = 0.34, arm: float = 0.7) -> Polygon:
    return unary_union([
        _arm(arm, width, 0.0), _arm(arm, width, 180.0), _arm(arm, width, 90.0),
    ]).buffer(0)


def shape_y(width: float = 0.34, arm: float = 0.85) -> Polygon:
    return unary_union([
        _arm(arm, width, 90.0), _arm(arm, width, 210.0), _arm(arm, width, 330.0),
    ]).buffer(0)


def shape_plus(width: float = 0.34, arm: float = 0.7) -> Polygon:
    return unary_union([
        _arm(arm, width, d) for d in (0.0, 90.0, 180.0, 270.0)
    ]).buffer(0)


def normalize(geom: Polygon, target_area: float) -> Polygon:
    """면적을 target_area 로 맞추고 무게중심을 원점으로 옮긴다."""
    factor = math.sqrt(target_area / geom.area)
    scaled = scale(geom, factor, factor, origin="centroid")
    centre = scaled.centroid
    return translate(scaled, -centre.x, -centre.y)


# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    code: str
    family: str
    label: str
    geom: Polygon          # 실좌표 배치 완료된 기준층 폴리곤
    floors: int

    @property
    def top_m(self) -> float:
        return self.floors * RESI_FLOOR_H + ROOFTOP_M

    @property
    def prism(self) -> H.Prism:
        return H.Prism(self.geom, self.top_m, self.code)

    @property
    def rectangularity(self) -> float:
        """직사각형도 – 면적 / 최소회전사각형 면적 (1.0 = 완전 직사각형)."""
        return self.geom.area / self.geom.minimum_rotated_rectangle.area

    @property
    def aspect(self) -> float:
        """최소회전사각형의 장변/단변 비."""
        rect = self.geom.minimum_rotated_rectangle
        xs, ys = rect.exterior.coords.xy
        sides = [math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(4)]
        long_side, short_side = max(sides), min(sides)
        return long_side / short_side if short_side else 99.0

    @property
    def ew_width(self) -> float:
        """동서 방향 폭 – 정오 그림자 폭을 지배한다."""
        minx, _miny, maxx, _maxy = self.geom.bounds
        return maxx - minx

    @property
    def squareness(self) -> float:
        """'네모난 정도' 종합 – 직사각형도와 정사각형 비율을 함께 본다."""
        return self.rectangularity * (1.0 / max(1.0, self.aspect)) ** 0.35



def push_away(geom: Polygon, site: Polygon, away: tuple[float, float],
              margin: float = 6.0) -> Polygon | None:
    """형상을 대지 중심에 놓은 뒤 학교 반대 방향으로 최대한 밀어 배치한다.

    대지가 볼록하므로 한 방향 평행이동에 대한 포함 여부는 구간을 이루어
    이분 탐색으로 최대 이동량을 구할 수 있다. 대지에 들어가지 않으면 None.
    """
    buildable = site.buffer(-margin)
    centre = site.centroid
    base = translate(geom, centre.x - geom.centroid.x, centre.y - geom.centroid.y)
    if not buildable.contains(base):
        return None
    ax, ay = away
    lo, hi = 0.0, 120.0
    while hi - lo > 0.1:
        mid = (lo + hi) / 2.0
        if buildable.contains(translate(base, mid * ax, mid * ay)):
            lo = mid
        else:
            hi = mid
    return translate(base, lo * ax, lo * ay)


def build_candidates(
    site: Polygon, away: tuple[float, float], floor_area: float, floors: int,
    rotations_rect: Sequence[float], rotations_free: Sequence[float],
) -> list[Candidate]:
    specs: list[tuple[str, str, Polygon, Sequence[float]]] = []

    for aspect in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0):
        specs.append((f"직사각형 {aspect:g}:1", "직사각형",
                      shape_rect(aspect), (0.0,) if aspect == 1.0 else rotations_rect))
    for cut in (0.15, 0.30):
        specs.append((f"모서리컷 팔각형 c{cut:g}", "팔각형",
                      shape_octagon(cut, 1.0), (0.0, 45.0)))
    specs.append(("모서리컷 팔각형 1.6:1", "팔각형",
                  shape_octagon(0.25, 1.6), rotations_rect))
    for aspect in (1.0, 1.6, 2.4):
        specs.append((f"타원 {aspect:g}:1", "타원",
                      shape_ellipse(aspect), (0.0,) if aspect == 1.0 else rotations_rect))
    for open_deg in (90.0, 120.0, 140.0, 160.0):
        specs.append((f"'<' 벌림 {open_deg:g}°", "V(<)형",
                      shape_v(open_deg), rotations_free))
    specs.append(("'ㄱ' 직각", "L(ㄱ)형", shape_l(), rotations_free))
    specs.append(("'T' 자", "T형", shape_t(), rotations_free))
    specs.append(("'Y' 자", "Y형", shape_y(), rotations_free))
    specs.append(("'+' 십자", "십자형", shape_plus(), (0.0, 45.0)))

    candidates: list[Candidate] = []
    index = 0
    for label, family, base, rotations in specs:
        unit = normalize(base, floor_area)
        for angle in rotations:
            placed = push_away(rotate(unit, angle, origin="centroid"), site, away)
            if placed is None:
                continue
            index += 1
            candidates.append(Candidate(
                f"S{index:03d}", family, f"{label} / 회전 {angle:g}°",
                placed, floors))
    return candidates


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="화랑아파트 1개동 평면 형상 최적화")
    parser.add_argument("--buildings", type=Path, required=True)
    parser.add_argument("--daegyo", type=Path,
                        default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    parser.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_shape"))
    parser.add_argument("--site-area", type=float, default=9395.0)
    parser.add_argument("--far", type=float, default=400.0)
    parser.add_argument("--bcr", type=float, default=60.0)
    parser.add_argument("--floors", type=int, default=55, help="기준 층수")
    parser.add_argument("--date", type=str, default="12-22")
    parser.add_argument("--school-radius", type=float, default=350.0)
    parser.add_argument("--step-min", type=int, default=10)
    parser.add_argument("--top-n", type=int, default=8, help="출력할 상위 형상 수")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)

    ctx = H.prepare_analysis(args)
    site, receptors, times = ctx["site"], ctx["receptors"], ctx["times"]
    context, existing = ctx["context"], ctx["existing_hwarang"]
    long_az, long_len = ctx["long_az"], ctx["long_len"]

    floor_area = args.site_area * args.far / 100.0 / args.floors
    # 앞선 배치 검토 결론: 타워는 학교 반대편(대지 남동측) 끝으로 밀어 배치
    ang = math.radians(long_az)
    vx, vy = math.sin(ang + math.pi / 2), math.cos(ang + math.pi / 2)
    away = (-ctx["school_side"] * vx, -ctx["school_side"] * vy)

    masks = H.build_context_masks(context, receptors, times)
    base_now = H.per_school(H.evaluate(receptors, existing, times, masks, args.step_min))
    stats_now = H.summarize(H.evaluate(receptors, existing, times, masks, args.step_min))
    vacant = H.summarize(H.evaluate(receptors, [], times, masks, args.step_min))

    print(f"기준: 1개동 {args.floors}층 · 기준층 {floor_area:,.0f}㎡ "
          f"(용적률 {args.far:.0f}% 고정) · 형상별로 대지 남동측 끝까지 밀어 배치")
    print(f"현황(10층 3개동) 기준A {stats_now['pass_pct']:.1f}% / "
          f"부지 공지 시 {vacant['pass_pct']:.1f}%\n")

    rot_rect = tuple(float(a) for a in range(0, 180, 15))
    rot_free = tuple(float(a) for a in range(0, 360, 30))
    candidates = build_candidates(site, away, floor_area, args.floors,
                                  rot_rect, rot_free)
    print(f"형상 후보 {len(candidates)}개 평가 중...")

    buildable = site.buffer(-3.0)
    rows: list[dict[str, Any]] = []
    for cand in candidates:
        results = H.evaluate(receptors, [cand.prism], times, masks, args.step_min)
        stats = H.summarize(results)
        schools = H.per_school(results)
        rows.append({
            "cand": cand,
            "stats": stats,
            "schools": schools,
            "delta_all": stats["pass_pct"] - stats_now["pass_pct"],
            "delta_min": min(schools[j] - base_now.get(j, 0.0) for j in schools),
            "fits": buildable.contains(cand.geom),
        })

    # 우선순위 ① 현황 대비 개선(전체·학교별) ② 네모난 정도 ③ 전체 충족률
    def tier(row: dict[str, Any]) -> int:
        if row["delta_all"] > 0 and row["delta_min"] >= 0:
            return 0                      # 모든 학교가 현황 이상
        if row["delta_all"] > 0 and row["delta_min"] >= -1.0:
            return 1                      # 전체 개선, 학교별 저하 1%p 이내
        if row["delta_all"] > 0:
            return 2                      # 전체 개선
        return 3
    # ① 현황 대비 개선폭(0.5%p 밴드 – 그 이하 차이는 모델 오차 범위)
    # ② 네모난 정도 ③ 개선폭 정밀값
    rows.sort(key=lambda r: (
        tier(r), -round(r["delta_all"] * 2) / 2,
        -round(r["cand"].squareness, 3), -r["delta_all"]))

    print(f"\n{'순위':>3} {'형상':<28}{'계열':<8}{'네모도':>7}{'장단비':>7}"
          f"{'EW폭':>7}{'기준A':>7}{'현황대비':>8}{'학교최악':>8}")
    print("-" * 88)
    for rank, row in enumerate(rows[:args.top_n], 1):
        c, st = row["cand"], row["stats"]
        print(f"{rank:>3} {c.label[:27]:<28}{c.family:<8}{c.rectangularity:7.2f}"
              f"{c.aspect:7.2f}{c.ew_width:6.1f}m{st['pass_pct']:6.1f}%"
              f"{row['delta_all']:+7.1f}%p{row['delta_min']:+7.1f}%p")

    print(f"\n계열별 최고 성적")
    print(f"{'계열':<10}{'최고 기준A':>10}{'현황대비':>9}{'최적 형상':<34}{'네모도':>7}")
    print("-" * 72)
    for family in ("직사각형", "팔각형", "타원", "V(<)형", "L(ㄱ)형", "T형", "Y형", "십자형"):
        subset = [r for r in rows if r["cand"].family == family]
        if not subset:
            continue
        best = max(subset, key=lambda r: (r["stats"]["pass_pct"], r["delta_min"]))
        print(f"{family:<10}{best['stats']['pass_pct']:9.1f}%{best['delta_all']:+8.1f}%p"
              f"  {best['cand'].label[:32]:<34}{best['cand'].rectangularity:6.2f}")

    write_outputs(args, site, rows, stats_now, vacant, base_now, floor_area)
    return 0


def write_outputs(args, site, rows, stats_now, vacant, base_now, floor_area) -> None:
    top = rows[:args.top_n]
    features = []
    for rank, row in enumerate(top, 1):
        c, st = row["cand"], row["stats"]
        features.append((c.geom, {
            "rank": rank, "code": c.code, "family": c.family, "label": c.label,
            "floors": c.floors, "height_m": round(c.top_m, 2),
            "base_elev_m": H.GROUND_ELEV_M,
            "top_elev_m": round(H.GROUND_ELEV_M + c.top_m, 2),
            "area_m2": round(c.geom.area, 1),
            "rectangularity": round(c.rectangularity, 3),
            "aspect": round(c.aspect, 2),
            "ew_width_m": round(c.ew_width, 1),
            "pass_a_pct": round(st["pass_pct"], 1),
            "delta_vs_now_pp": round(row["delta_all"], 1),
            "fits_site": bool(row["fits"]),
        }))
    payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
        "features": [{"type": "Feature", "geometry": mapping(g), "properties": p}
                     for g, p in features],
    }
    (args.outdir / "hwarang_shape_top.geojson").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    schools = sorted(rows[0]["schools"])
    with (args.outdir / "shape_comparison.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["순위", "코드", "계열", "형상", "기준층㎡", "층수",
                         "직사각형도", "장단비", "동서폭m", "대지수용",
                         "기준A충족%", "기준B충족%", "현황대비%p", "학교최악%p",
                         "불충족점수", "불충족평균확보h"]
                        + [f"학교{j}%" for j in schools])
        for rank, row in enumerate(rows, 1):
            c, st = row["cand"], row["stats"]
            writer.writerow([
                rank, c.code, c.family, c.label, round(c.geom.area, 1), c.floors,
                round(c.rectangularity, 3), round(c.aspect, 2), round(c.ew_width, 1),
                "가능" if row["fits"] else "초과",
                round(st["pass_pct"], 1), round(st["pass_strict_pct"], 1),
                round(row["delta_all"], 1), round(row["delta_min"], 1),
                st["n_fail"], round(st["fail_mean_total_h"], 2),
            ] + [round(row["schools"][j], 1) for j in schools])

    export_shape_sheet(args, site, top, stats_now, vacant, floor_area)
    best = top[0]
    print(f"\n권장 형상: [{best['cand'].code}] {best['cand'].label} "
          f"({best['cand'].family})")
    print(f"  기준A {best['stats']['pass_pct']:.1f}% "
          f"(현황 {stats_now['pass_pct']:.1f}% 대비 {best['delta_all']:+.1f}%p, "
          f"공지 {vacant['pass_pct']:.1f}%)")
    print(f"결과 저장: {args.outdir}")


def export_shape_sheet(args, site, top, stats_now, vacant, floor_area) -> None:
    """상위 형상을 한 장에 비교하는 SVG 시트."""
    cols, cell, pad = 4, 240, 16
    rows_n = (len(top) + cols - 1) // cols
    width, height = cols * cell, rows_n * cell + 46
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>',
           f'<text x="12" y="26" font-family="sans-serif" font-size="17" '
           f'font-weight="bold" fill="#111">화랑아파트 1개동 {args.floors}층 · '
           f'기준층 {floor_area:,.0f}㎡ – 평면 형상 비교 (상단이 북)</text>']
    for i, row in enumerate(top):
        c, st = row["cand"], row["stats"]
        ox, oy = (i % cols) * cell, 46 + (i // cols) * cell
        minx, miny, maxx, maxy = c.geom.bounds
        span = max(maxx - minx, maxy - miny) * 1.35 or 1.0
        s = (cell - 2 * pad - 44) / span
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        pts = " ".join(
            f"{ox + cell / 2 + (x - cx) * s:.1f},{oy + cell / 2 + 12 - (y - cy) * s:.1f}"
            for x, y in c.geom.exterior.coords)
        colour = "#1d3557" if row["delta_all"] > 0 else "#9a3b3b"
        out.append(f'<rect x="{ox + 4}" y="{oy + 2}" width="{cell - 8}" '
                   f'height="{cell - 8}" fill="#fff" stroke="#ddd"/>')
        out.append(f'<polygon points="{pts}" fill="{colour}" fill-opacity="0.85" '
                   f'stroke="#0b1c30" stroke-width="1.5"/>')
        out.append(f'<text x="{ox + 12}" y="{oy + 26}" font-family="sans-serif" '
                   f'font-size="12.5" font-weight="bold" fill="#111">'
                   f'{i + 1}. {c.label[:26]}</text>')
        out.append(f'<text x="{ox + 12}" y="{oy + cell - 30}" font-family="sans-serif" '
                   f'font-size="12" fill="#333">기준A {st["pass_pct"]:.1f}% '
                   f'(현황 {row["delta_all"]:+.1f}%p)</text>')
        out.append(f'<text x="{ox + 12}" y="{oy + cell - 14}" font-family="sans-serif" '
                   f'font-size="12" fill="#555">네모도 {c.rectangularity:.2f} · '
                   f'장단비 {c.aspect:.2f} · EW {c.ew_width:.0f}m</text>')
    out.append("</svg>")
    (args.outdir / "hwarang_shape_sheet.svg").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
