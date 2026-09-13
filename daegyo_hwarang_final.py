#!/usr/bin/env python3
"""대교·화랑아파트 재건축 – 학교 일조권 최종 종합 분석.

4개 시나리오 × 학교별 일조시간 × 아파트별 영향도를 한 번에 산출하고,
학교 교사동을 식별할 수 있는 배치도를 별도로 생성한다.

시나리오
    S0 재건축 전(현황)   대교 기존 12층 + 화랑 기존 10층
    S1 대교 단독 재건축   대교 인가안(최고 48층) + 화랑 기존
    S2 화랑 단독 재건축   대교 기존 + 화랑 확정안(55층 1개동)
    S3 동시 재건축       대교 인가안 + 화랑 확정안

아파트별 영향도는 '반사실(counterfactual)' 로 계산한다.
    대교 영향도 = (대교를 지웠을 때의 일조시간) − (실제 일조시간)
    화랑 영향도 = (화랑을 지웠을 때의 일조시간) − (실제 일조시간)
그림자가 겹치는 구간이 이중계상되지 않으므로, 두 값의 합이 두 단지를 모두
지웠을 때의 회복량보다 작을 수 있다(그 차이가 곧 '중첩분').
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union

import hwarang_massing_study as H
from daegyo_school_sunlight import (
    DAEGYO_JIBUN, HWARANG_JIBUN, TOWER_MIN_H,
    apartment_prisms, load_hwarang_plan, load_named_buildings,
    resolved_height, school_label, school_parcel_rows,
)
from daegyo_school_hours import (
    building_receptors, dong_labels, ground_receptors, playground_area,
)


@dataclass
class Unit:
    """분석 단위(교사동 1개동 또는 운동장 1개소)."""
    label: str
    kind: str                 # '교사동' | '운동장'
    jibun: str
    school: str
    floors: int
    geom: Polygon
    n: int = 0
    hours: dict[str, float] = field(default_factory=dict)      # 시나리오→일조시간
    cont: dict[str, float] = field(default_factory=dict)
    passes: dict[str, float] = field(default_factory=dict)     # 기준A 충족률
    daegyo: dict[str, float] = field(default_factory=dict)     # 시나리오→대교 영향도
    hwarang: dict[str, float] = field(default_factory=dict)
    both: dict[str, float] = field(default_factory=dict)


SCEN_NAMES = {
    "S0": "재건축 전(현황)",
    "S1": "대교 단독 재건축",
    "S2": "화랑 단독 재건축",
    "S3": "동시 재건축",
}


def mean_hours(results: Sequence[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """수광점 결과를 단위(building 라벨)별 평균으로 집계."""
    tot: dict[str, list[float]] = defaultdict(list)
    con: dict[str, list[float]] = defaultdict(list)
    ok: dict[str, list[int]] = defaultdict(list)
    for r in results:
        tot[r["building"]].append(r["total_h_08_16"])
        con[r["building"]].append(r["cont_h_08_16"])
        ok[r["building"]].append(
            1 if (r["cont_h_08_16"] >= 2.0 or r["total_h_08_16"] >= 4.0) else 0)
    return {
        "hours": {k: sum(v) / len(v) for k, v in tot.items()},
        "cont": {k: sum(v) / len(v) for k, v in con.items()},
        "pass": {k: sum(v) / len(v) * 100.0 for k, v in ok.items()},
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="대교·화랑 재건축 학교 일조권 최종 종합")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--plan", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--hwarang-plan", type=Path,
                   default=Path("outputs/hwarang_final/hwarang_final_massing.geojson"))
    p.add_argument("--playground", type=Path, default=None,
                   help="실제 운동장 폴리곤 GeoJSON(없으면 교사동 주변 옥외지반으로 근사)")
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--outdir", type=Path, default=Path("outputs/daegyo_final"))
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    from pyproj import CRS, Transformer

    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    daegyo_plan = H.load_daegyo(args.plan)
    site = unary_union([p.footprint for p in daegyo_plan])
    centre = site.centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)
    all_buildings = unary_union([r["geom"] for r in buildings])

    schools = school_parcel_rows(buildings, centre, args.school_radius)
    print("■ 학교 부지 건물 인식 결과 (용도·층수 누락분을 A16 높이로 복구)")
    for jibun, rows in sorted(schools.items()):
        cls = [r for r in rows if r["classroom"]]
        rec = [r for r in rows if not r["use"]]
        print(f"   {school_label(jibun, rows):<24} 전체 {len(rows):2d}동 "
              f"(교실동 {len(cls):2d} / 부속·차폐만 {len(rows)-len(cls):2d}) "
              f"· 속성누락 복구 {len(rec):2d}동")
    print()

    user_pg: dict[str, Polygon] = {}
    if args.playground and args.playground.exists():
        data = json.loads(args.playground.read_text(encoding="utf-8"))
        for f in data["features"]:
            key = str(f["properties"].get("jibun") or f["properties"].get("school"))
            user_pg[key] = shape(f["geometry"])

    # ── 분석 단위와 수광점 ────────────────────────────────────────────────
    units: dict[str, Unit] = {}
    receptors: list[H.Receptor] = []
    for jibun, rows in sorted(schools.items()):
        school = school_label(jibun, rows).split(" ", 1)[1]
        # 교실동만 수광점 대상(부속동·창고는 차폐물로만 작용)
        classrooms = [r for r in rows if r["classroom"]]
        for label, row in dong_labels(classrooms, school):
            rec = building_receptors([row], label)
            receptors += rec
            units[label] = Unit(label, "교사동", jibun, school, row["floors"],
                                row["geom"], len(rec))
        sb = unary_union([r["geom"] for r in rows])
        pg = user_pg.get(jibun) or playground_area(sb, all_buildings, args.apron)
        label = f"{school} 운동장"
        rec = ground_receptors(pg, label)
        receptors += rec
        units[label] = Unit(label, "운동장", jibun, school, 0, pg, len(rec))

    print(f"분석 단위 {len(units)}개(교사동 "
          f"{sum(1 for u in units.values() if u.kind=='교사동')} + 운동장 "
          f"{sum(1 for u in units.values() if u.kind=='운동장')}) · "
          f"수광점 {len(receptors):,}개")
    print(f"기준일 {args.date}(동지) 08~16시 {args.step_min}분 간격 "
          f"{len(times)}개 시점\n")

    # ── 차폐물 ────────────────────────────────────────────────────────────
    # 차폐물: 대교·화랑 대지 외 모든 기존 건물(학교 부속동 포함).
    # 높이는 A16 실측 우선 → 없으면 층수 환산(resolved_height)
    context: list[H.Prism] = []
    n_by_height = 0
    for row in buildings:
        if row["jibun"] in (DAEGYO_JIBUN, HWARANG_JIBUN):
            continue
        if row["geom"].centroid.distance(centre) > args.context_radius:
            continue
        height = resolved_height(row)
        if height <= 0.0:
            continue
        if row["floors"] <= 0:
            n_by_height += 1
        geom = row["geom"]
        for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
            context.append(H.Prism(poly, height, "기존건물"))
    print(f"주변 차폐물 {len(context)}개 "
          f"(이 중 {n_by_height}개는 층수 누락분을 A16 높이로 복구)")
    masks = H.build_context_masks(context, receptors, times)

    d_now = apartment_prisms(buildings, DAEGYO_JIBUN, "대교 기존")
    h_now = apartment_prisms(buildings, HWARANG_JIBUN, "화랑 기존")
    d_new = daegyo_plan
    h_new = load_hwarang_plan(args.hwarang_plan)
    if not h_new:
        raise SystemExit("화랑 확정 매싱 파일을 찾지 못했습니다.")

    combos = {
        "S0": d_now + h_now, "S1": d_new + h_now,
        "S2": d_now + h_new, "S3": d_new + h_new,
        # 반사실: 한쪽 단지를 지운 상태
        "_D없음_화랑기존": h_now, "_D없음_화랑신축": h_new,
        "_H없음_대교기존": d_now, "_H없음_대교신축": d_new,
        "_둘다없음": [],
    }
    agg: dict[str, dict[str, dict[str, float]]] = {}
    for key, prisms in combos.items():
        res = H.evaluate(receptors, prisms, times, masks, args.step_min)
        agg[key] = mean_hours(res)
        tag = SCEN_NAMES.get(key, key)
        print(f"  평가 완료: {tag}")

    # 시나리오별 반사실 대응표
    no_daegyo = {"S0": "_D없음_화랑기존", "S1": "_D없음_화랑기존",
                 "S2": "_D없음_화랑신축", "S3": "_D없음_화랑신축"}
    no_hwarang = {"S0": "_H없음_대교기존", "S1": "_H없음_대교신축",
                  "S2": "_H없음_대교기존", "S3": "_H없음_대교신축"}

    for label, u in units.items():
        for code in ("S0", "S1", "S2", "S3"):
            u.hours[code] = agg[code]["hours"][label]
            u.cont[code] = agg[code]["cont"][label]
            u.passes[code] = agg[code]["pass"][label]
            u.daegyo[code] = agg[no_daegyo[code]]["hours"][label] - u.hours[code]
            u.hwarang[code] = agg[no_hwarang[code]]["hours"][label] - u.hours[code]
            u.both[code] = agg["_둘다없음"]["hours"][label] - u.hours[code]

    # ── 학교 단위 집계 ────────────────────────────────────────────────────
    by_school: dict[str, list[Unit]] = defaultdict(list)
    for u in units.values():
        by_school[u.jibun].append(u)

    def w(us: Sequence[Unit], attr: str, code: str) -> float:
        n = sum(x.n for x in us)
        return sum(getattr(x, attr)[code] * x.n for x in us) / n if n else 0.0

    order = sorted(by_school, key=lambda j: w(by_school[j], "hours", "S0"))

    print("\n" + "=" * 92)
    print("표1. 학교별 평균 일조시간 (동지일 08~16시, 교사동 창면 + 운동장 지반 통합)")
    print("=" * 92)
    print(f"{'학교':<24}{'수광점':>6}{'S0 현황':>10}{'S1 대교':>10}{'S2 화랑':>10}"
          f"{'S3 동시':>10}{'S1−S0':>9}{'S2−S0':>9}{'S3−S0':>9}")
    print("-" * 92)
    for j in order:
        us = by_school[j]
        h = [w(us, "hours", c) for c in ("S0", "S1", "S2", "S3")]
        print(f"{j} {us[0].school:<20}{sum(x.n for x in us):>5}"
              f"{h[0]:>9.2f}h{h[1]:>9.2f}h{h[2]:>9.2f}h{h[3]:>9.2f}h"
              f"{h[1]-h[0]:>+9.2f}{h[2]-h[0]:>+9.2f}{h[3]-h[0]:>+9.2f}")
    allu = list(units.values())
    h = [w(allu, "hours", c) for c in ("S0", "S1", "S2", "S3")]
    print("-" * 92)
    print(f"{'전체 평균':<24}{sum(x.n for x in allu):>5}"
          f"{h[0]:>9.2f}h{h[1]:>9.2f}h{h[2]:>9.2f}h{h[3]:>9.2f}h"
          f"{h[1]-h[0]:>+9.2f}{h[2]-h[0]:>+9.2f}{h[3]-h[0]:>+9.2f}")

    print("\n" + "=" * 92)
    print("표2. 학교별 아파트 영향도 — 해당 단지를 지웠을 때 회복되는 시간(반사실)")
    print("=" * 92)
    print(f"{'학교':<22}{'시나리오':<16}{'대교 영향':>11}{'화랑 영향':>11}"
          f"{'합계(단순)':>12}{'둘다제거':>11}{'중첩분':>9}")
    print("-" * 92)
    for j in order:
        us = by_school[j]
        for code in ("S0", "S1", "S2", "S3"):
            d, hh, b = (w(us, "daegyo", code), w(us, "hwarang", code),
                        w(us, "both", code))
            print(f"{(us[0].school if code=='S0' else ''):<22}{SCEN_NAMES[code]:<16}"
                  f"{d:>10.2f}h{hh:>10.2f}h{d+hh:>11.2f}h{b:>10.2f}h{b-(d+hh):>+8.2f}h")
        print("-" * 92)

    print("\n" + "=" * 100)
    print("표3. 교사동·운동장별 상세 일조시간")
    print("=" * 100)
    print(f"{'구분':<26}{'종류':<7}{'수광점':>6}{'S0':>8}{'S1':>8}{'S2':>8}{'S3':>8}"
          f"{'S3−S0':>9}{'S3 대교':>9}{'S3 화랑':>9}")
    print("-" * 100)
    for j in order:
        for u in sorted(by_school[j], key=lambda x: (x.kind != "교사동", x.label)):
            print(f"{u.label:<26}{u.kind:<7}{u.n:>6}{u.hours['S0']:>8.2f}"
                  f"{u.hours['S1']:>8.2f}{u.hours['S2']:>8.2f}{u.hours['S3']:>8.2f}"
                  f"{u.hours['S3']-u.hours['S0']:>+9.2f}"
                  f"{u.daegyo['S3']:>9.2f}{u.hwarang['S3']:>9.2f}")

    print("\n" + "=" * 92)
    print("표4. 참고 – 기준A 충족률(%) : 문턱 지표이므로 시간과 함께 볼 것")
    print("=" * 92)
    print(f"{'학교':<24}{'S0 현황':>10}{'S1 대교':>10}{'S2 화랑':>10}{'S3 동시':>10}")
    print("-" * 92)
    for j in order:
        us = by_school[j]
        p = [w(us, "passes", c) for c in ("S0", "S1", "S2", "S3")]
        print(f"{j} {us[0].school:<20}{p[0]:>9.1f}%{p[1]:>9.1f}%"
              f"{p[2]:>9.1f}%{p[3]:>9.1f}%")

    write_outputs(args, units, by_school, order, d_now, d_new, h_now, h_new,
                  context, site)
    return 0


# --------------------------------------------------------------------------- #
def write_outputs(args, units, by_school, order, d_now, d_new, h_now, h_new,
                  context, site) -> None:
    out = args.outdir
    codes = ("S0", "S1", "S2", "S3")

    def w(us, attr, code):
        n = sum(x.n for x in us)
        return sum(getattr(x, attr)[code] * x.n for x in us) / n if n else 0.0

    with (out / "final_school_hours.csv").open("w", encoding="utf-8-sig",
                                               newline="") as fp:
        wr = csv.writer(fp)
        wr.writerow(["학교", "지번", "수광점"]
                    + [f"{c} {SCEN_NAMES[c]} 일조h" for c in codes]
                    + ["S1-S0", "S2-S0", "S3-S0"]
                    + [f"{c} 대교영향h" for c in codes]
                    + [f"{c} 화랑영향h" for c in codes]
                    + [f"{c} 기준A%" for c in codes])
        for j in order:
            us = by_school[j]
            h = [w(us, "hours", c) for c in codes]
            wr.writerow([us[0].school, j, sum(x.n for x in us)]
                        + [round(v, 2) for v in h]
                        + [round(h[1] - h[0], 2), round(h[2] - h[0], 2),
                           round(h[3] - h[0], 2)]
                        + [round(w(us, "daegyo", c), 2) for c in codes]
                        + [round(w(us, "hwarang", c), 2) for c in codes]
                        + [round(w(us, "passes", c), 1) for c in codes])

    with (out / "final_unit_hours.csv").open("w", encoding="utf-8-sig",
                                             newline="") as fp:
        wr = csv.writer(fp)
        wr.writerow(["구분", "종류", "학교", "지번", "층수", "수광점", "면적m2",
                     "중심X", "중심Y"]
                    + [f"{c} 일조h" for c in codes]
                    + [f"{c} 연속h" for c in codes]
                    + [f"{c} 기준A%" for c in codes]
                    + [f"{c} 대교영향h" for c in codes]
                    + [f"{c} 화랑영향h" for c in codes])
        for j in order:
            for u in sorted(by_school[j], key=lambda x: (x.kind != "교사동", x.label)):
                wr.writerow([u.label, u.kind, u.school, u.jibun, u.floors, u.n,
                             round(u.geom.area, 1),
                             round(u.geom.centroid.x, 1), round(u.geom.centroid.y, 1)]
                            + [round(u.hours[c], 2) for c in codes]
                            + [round(u.cont[c], 2) for c in codes]
                            + [round(u.passes[c], 1) for c in codes]
                            + [round(u.daegyo[c], 2) for c in codes]
                            + [round(u.hwarang[c], 2) for c in codes])

    # 교사동 식별용 GeoJSON
    feats = []
    for u in units.values():
        feats.append({
            "type": "Feature", "geometry": mapping(u.geom),
            "properties": {
                "label": u.label, "kind": u.kind, "school": u.school,
                "jibun": u.jibun, "floors": u.floors, "receptors": u.n,
                **{f"{c}_hours": round(u.hours[c], 2) for c in codes},
                **{f"{c}_daegyo_h": round(u.daegyo[c], 2) for c in codes},
                **{f"{c}_hwarang_h": round(u.hwarang[c], 2) for c in codes},
            }})
    (out / "final_school_units.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
        "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")

    export_site_plan(out / "final_site_plan.svg", units, by_school, order,
                     d_now, d_new, h_now, h_new, context)
    print(f"\n결과 저장: {out}")


def export_site_plan(path: Path, units, by_school, order, d_now, d_new,
                     h_now, h_new, context) -> None:
    """학교 교사동을 식별할 수 있는 배치도(별도 도면)."""
    school_units = [u for u in units.values() if u.kind == "교사동"]
    pg_units = [u for u in units.values() if u.kind == "운동장"]
    layers = ([u.geom for u in units.values()]
              + [p.footprint for p in (*d_now, *d_new, *h_now, *h_new)])
    minx, miny, maxx, maxy = unary_union(layers).buffer(30.0).bounds
    px_w = 1240.0
    scale = px_w / (maxx - minx)
    px_h = (maxy - miny) * scale
    top = 108.0          # 제목 영역
    bottom = 136.0       # 범례 영역(2줄)

    def path_of(geom, dx: float = 0.0, dy: float = 0.0) -> str:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return "".join(
            '<polygon points="' + " ".join(
                f"{(x - minx) * scale + dx:.1f},{(maxy - y) * scale + dy:.1f}"
                for x, y in poly.exterior.coords) + '" />' for poly in polys)

    def xy(pt) -> tuple[float, float]:
        return (pt.x - minx) * scale, (maxy - pt.y) * scale

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h + top + bottom:.0f}" '
           f'viewBox="0 0 {px_w:.0f} {px_h + top + bottom:.0f}">',
           '<rect width="100%" height="100%" fill="#fcfcfa"/>',
           '<text x="16" y="30" font-family="sans-serif" font-size="19" '
           'font-weight="bold" fill="#111">대교·화랑아파트 재건축 – 학교 교사동 '
           '배치도 (교사동 번호는 각 학교 내 북→남 순)</text>',
           '<text x="16" y="54" font-family="sans-serif" font-size="12.5" fill="#555">'
           '각 교사동 아래 숫자 = 동지일 평균 일조시간(시간): '
           '재건축전 → 대교단독 → 화랑단독 → 동시재건축</text>',
           '<text x="16" y="74" font-family="sans-serif" font-size="12.5" fill="#555">'
           '운동장 영역은 지적 경계 자료가 없어 교사동 주변 옥외지반으로 근사한 '
           '것이며, 절대값보다 시나리오 간 차이를 보십시오.</text>',
           f'<g transform="translate(0,{top:.0f})">']

    # 운동장(근사) → 기존 아파트 → 신축 → 학교 순으로 겹쳐 그림
    for u in pg_units:
        svg.append(f'<g fill="#e4eeda" fill-opacity="0.38" stroke="#9ab37c" '
                   f'stroke-width="1" stroke-dasharray="5 4">{path_of(u.geom)}</g>')
    for prism in context:
        svg.append(f'<g fill="#e6e6e1" fill-opacity="0.7" stroke="#cfcfc8" '
                   f'stroke-width="0.5">{path_of(prism.footprint)}</g>')
    for prism in (*d_now, *h_now):
        svg.append(f'<g fill="#ffffff" fill-opacity="0.35" stroke="#8d99ae" '
                   f'stroke-width="1.5" stroke-dasharray="7 4">'
                   f'{path_of(prism.footprint)}</g>')
    for prism in (*d_new, *h_new):
        tall = prism.top_m >= TOWER_MIN_H
        fill = "#1d3557" if tall else "#adb5bd"
        svg.append(f'<g fill="{fill}" fill-opacity="0.9" stroke="#0b1c30" '
                   f'stroke-width="1.2">{path_of(prism.footprint)}</g>')
        if tall:
            cx, cy = xy(prism.footprint.centroid)
            svg.append(f'<text x="{cx:.1f}" y="{cy + 3:.1f}" font-family="sans-serif" '
                       f'font-size="9" text-anchor="middle" fill="#fff">'
                       f'{prism.label.split()[-1]}</text>')

    for u in school_units:
        delta = u.hours["S3"] - u.hours["S0"]
        fill = ("#c1121f" if delta <= -1.0 else "#e07a5f" if delta <= -0.3
                else "#81b29a" if delta > 0.1 else "#a8a8a0")
        svg.append(f'<g fill="{fill}" fill-opacity="0.92" stroke="#2b2b2b" '
                   f'stroke-width="1.3">{path_of(u.geom)}</g>')
        cx, cy = xy(u.geom.centroid)
        mark = u.label.rsplit(" ", 1)[-1]
        svg.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="sans-serif" '
                   f'font-size="13" font-weight="bold" text-anchor="middle" '
                   f'fill="#fff" stroke="#000" stroke-width="0.5" '
                   f'paint-order="stroke">{mark}</text>')
        svg.append(f'<text x="{cx:.1f}" y="{cy + 14:.1f}" font-family="sans-serif" '
                   f'font-size="10" text-anchor="middle" fill="#111">'
                   f'{u.hours["S0"]:.1f}→{u.hours["S1"]:.1f}→{u.hours["S2"]:.1f}'
                   f'→{u.hours["S3"]:.1f}h</text>')

    # 학교명 라벨 – 교사동군 위쪽(북측) 바깥에 두어 동별 라벨과 겹치지 않게
    for j in order:
        us = by_school[j]
        g = unary_union([x.geom for x in us if x.kind == "교사동"])
        cx = (g.centroid.x - minx) * scale
        cy = (maxy - g.bounds[3]) * scale - 10.0
        svg.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="sans-serif" '
                   f'font-size="14.5" font-weight="bold" text-anchor="middle" '
                   f'fill="#0b1c30" stroke="#fcfcfa" stroke-width="4" '
                   f'paint-order="stroke">{j} {us[0].school}</text>')

    # 방위표 · 스케일바
    svg.append(f'<g transform="translate({px_w - 74:.0f},26)">'
               '<line x1="0" y1="44" x2="0" y2="4" stroke="#111" stroke-width="2"/>'
               '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
               '<text x="0" y="60" font-family="sans-serif" font-size="12" '
               'text-anchor="middle" fill="#111">N</text></g>')
    bar_m = 50.0
    bx, by = 20.0, px_h - 20.0
    svg.append(f'<g><line x1="{bx}" y1="{by}" x2="{bx + bar_m * scale:.1f}" '
               f'y2="{by}" stroke="#111" stroke-width="3"/>'
               f'<text x="{bx + bar_m * scale / 2:.1f}" y="{by - 7:.0f}" '
               f'font-family="sans-serif" font-size="11.5" text-anchor="middle" '
               f'fill="#111">{bar_m:.0f} m</text></g>')
    svg.append("</g>")

    legend = [("#c1121f", "교사동 1h 이상 감소"), ("#e07a5f", "0.3~1h 감소"),
              ("#a8a8a0", "거의 변화 없음"), ("#81b29a", "증가"),
              ("#e4eeda", "운동장(근사)"), ("#1d3557", "신축 타워"),
              ("#adb5bd", "신축 저층부"), ("#ffffff", "기존 아파트(점선)")]
    for i, (colour, text) in enumerate(legend):
        x = 18 + (i % 4) * 300
        y = px_h + top + 30 + (i // 4) * 34
        svg.append(f'<rect x="{x}" y="{y - 11}" width="14" height="14" fill="{colour}" '
                   f'fill-opacity="0.9" stroke="#333" stroke-width="0.7"/>')
        svg.append(f'<text x="{x + 21}" y="{y + 1}" font-family="sans-serif" '
                   f'font-size="12" fill="#111">{text}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
