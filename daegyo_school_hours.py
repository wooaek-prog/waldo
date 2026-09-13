#!/usr/bin/env python3
"""학교별·교사동별·운동장별 '일조시간(시간)' 기준 비교.

충족률(%)이 아니라 실제 일조시간으로, 그리고 학교 단위가 아니라
교사동(건물) 단위와 운동장 단위로 나누어 다음 4개 시나리오를 비교한다.

    S0 현황            대교 기존 12층 + 화랑 기존 10층
    S1 대교 신축안      대교 인가안(최고 48층) + 화랑 기존
    S2 화랑 재건축      대교 기존 + 화랑 확정안(55층 1개동)
    S3 동시 재건축      대교 인가안 + 화랑 확정안

또한 시간대별로 '누가 가리고 있는지'(화랑/대교/기타 기존건물/자기그늘)를
분해해, 동시 재건축 시 일조가 개선되는 이유를 시간 단위로 설명한다.

운동장은 지적 경계 자료가 없어 '교사동에서 일정 거리 내 옥외 지반'으로
근사한다(--playground 로 실제 운동장 폴리곤을 주면 그것을 사용).
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

from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union
from shapely.prepared import prep

import hwarang_massing_study as H
from daegyo_school_sunlight import (
    APT_FLOOR_H, APT_ROOF_M, DAEGYO_JIBUN, HWARANG_JIBUN, TOWER_MIN_H,
    apartment_prisms, load_hwarang_plan, load_named_buildings, school_label,
)

GROUND_Z = 0.0          # 운동장 지반 수광 높이


# --------------------------------------------------------------------------- #
@dataclass
class Scen:
    code: str
    name: str
    daegyo: list[H.Prism]
    hwarang: list[H.Prism]

    @property
    def prisms(self) -> list[H.Prism]:
        return [*self.daegyo, *self.hwarang]


def building_receptors(rows: Sequence[dict[str, Any]], label: str,
                       step_m: float = 5.0) -> list[H.Receptor]:
    """교사동 1개동의 창면 수광점(기존 make_receptors를 동 단위로 사용)."""
    return H.make_receptors([{**r, "jibun": label} for r in rows], step_m)


def ground_receptors(area: Polygon, label: str, step_m: float = 8.0) -> list[H.Receptor]:
    """운동장 지반 격자 수광점(수평면 – 창면 방위 제약 없음)."""
    minx, miny, maxx, maxy = area.bounds
    ready = prep(area)
    pts: list[H.Receptor] = []
    y = miny + step_m / 2
    while y <= maxy:
        x = minx + step_m / 2
        while x <= maxx:
            if ready.contains(Point(x, y)):
                pts.append(H.Receptor(x, y, GROUND_Z, 180.0, label, 0, True))
            x += step_m
        y += step_m
    return pts


def playground_area(school_geoms: Polygon, all_buildings, apron_m: float) -> Polygon:
    """교사동 주변 옥외 지반(운동장 근사)."""
    region = school_geoms.buffer(apron_m)
    return region.difference(all_buildings.buffer(1.0))


def dong_labels(rows: Sequence[dict[str, Any]], school: str) -> list[tuple[str, dict]]:
    """학교 내 교사동에 북→남 순으로 ①②③ 라벨을 부여한다."""
    ordered = sorted(rows, key=lambda r: -r["geom"].centroid.y)
    marks = "①②③④⑤⑥⑦⑧⑨"
    out = []
    for i, row in enumerate(ordered):
        mark = marks[i] if i < len(marks) else f"#{i + 1}"
        out.append((f"{school} {mark}({row['floors']}층)", row))
    return out


# --------------------------------------------------------------------------- #
def attribution(receptors, times, groups: dict[str, list[H.Prism]]):
    """시각별로 각 차폐물 그룹이 가리는 수광점 수를 센다.

    반환: {그룹명: [시각별 차폐 수광점 수]}, 그리고 '일조'(전부 통과) 수.
    """
    heights = sorted({round(r.z, 2) for r in receptors})
    by_height: dict[float, list[int]] = defaultdict(list)
    for idx, r in enumerate(receptors):
        by_height[round(r.z, 2)].append(idx)
    cloud = H.receptor_cloud(receptors)

    counts = {name: [0] * len(times) for name in groups}
    counts["자기그늘(창면방위)"] = [0] * len(times)
    sunny = [0] * len(times)

    for t_index, (_clock, altitude, azimuth) in enumerate(times):
        for height in heights:
            per_group = {name: H.shadow_mask(prisms, height, azimuth, altitude, cloud)
                         for name, prisms in groups.items()}
            for idx in by_height[height]:
                r = receptors[idx]
                if not r.horizontal:
                    delta = abs((azimuth - r.normal_az + 180) % 360 - 180)
                    if delta >= 88.0:
                        counts["자기그늘(창면방위)"][t_index] += 1
                        continue
                pt = Point(r.x, r.y)
                blocked = False
                for name, mask in per_group.items():
                    if mask is not None and mask.contains(pt):
                        counts[name][t_index] += 1
                        blocked = True
                if not blocked:
                    sunny[t_index] += 1
    return counts, sunny


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="학교 교사동·운동장별 일조시간 비교")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--plan", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--hwarang-plan", type=Path,
                   default=Path("outputs/hwarang_final/hwarang_final_massing.geojson"))
    p.add_argument("--playground", type=Path, default=None,
                   help="실제 운동장 폴리곤 GeoJSON(속성 jibun 또는 school 포함)")
    p.add_argument("--apron", type=float, default=60.0,
                   help="운동장 근사 시 교사동 주변 옥외지반 반경(m)")
    p.add_argument("--outdir", type=Path, default=Path("outputs/daegyo_school"))
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--focus", type=str, default="40-2",
                   help="시간대 차폐원인 분해를 상세 출력할 학교 지번")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    from pyproj import CRS, Transformer

    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    plan_prisms = H.load_daegyo(args.plan)
    site = unary_union([p.footprint for p in plan_prisms])
    centre = site.centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)
    all_buildings = unary_union([r["geom"] for r in buildings])

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in buildings:
        if (row["use"] == H.SCHOOL_USE and row["floors"] > 0
                and row["geom"].centroid.distance(centre) <= args.school_radius):
            groups[row["jibun"]].append(row)

    # ── 수광점: 교사동별 창면 + 운동장 지반 ────────────────────────────────
    user_pg = {}
    if args.playground and args.playground.exists():
        data = json.loads(args.playground.read_text(encoding="utf-8"))
        for f in data["features"]:
            key = str(f["properties"].get("jibun") or f["properties"].get("school"))
            user_pg[key] = shape(f["geometry"])

    receptors: list[H.Receptor] = []
    unit_kind: dict[str, str] = {}
    unit_info: dict[str, dict[str, Any]] = {}
    for jibun, rows in groups.items():
        school = school_label(jibun, rows).split(" ", 1)[1]
        for label, row in dong_labels(rows, school):
            rec = building_receptors([row], label)
            receptors += rec
            unit_kind[label] = "교사동"
            unit_info[label] = {"jibun": jibun, "floors": row["floors"],
                                "n": len(rec), "area": round(row["geom"].area, 1),
                                "x": round(row["geom"].centroid.x, 1),
                                "y": round(row["geom"].centroid.y, 1)}
        sb = unary_union([r["geom"] for r in rows])
        pg = user_pg.get(jibun) or playground_area(sb, all_buildings, args.apron)
        label = f"{school} 운동장"
        rec = ground_receptors(pg, label)
        receptors += rec
        unit_kind[label] = "운동장"
        unit_info[label] = {"jibun": jibun, "floors": 0, "n": len(rec),
                            "area": round(pg.area, 1),
                            "x": round(pg.centroid.x, 1), "y": round(pg.centroid.y, 1)}

    print(f"수광점 {len(receptors):,}개 "
          f"(교사동 창면 {sum(1 for r in receptors if not r.horizontal):,} / "
          f"운동장 지반 {sum(1 for r in receptors if r.horizontal):,})")
    print(f"운동장은 {'사용자 제공 폴리곤' if user_pg else f'교사동 {args.apron:.0f}m 주변 옥외지반(근사)'}\n")

    # ── 차폐물 ────────────────────────────────────────────────────────────
    context: list[H.Prism] = []
    for row in buildings:
        if row["jibun"] in (DAEGYO_JIBUN, HWARANG_JIBUN) or row["floors"] <= 0:
            continue
        if row["geom"].centroid.distance(centre) > args.context_radius:
            continue
        geom = row["geom"]
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            context.append(H.Prism(poly, H.building_height(row), "기존건물"))
    masks = H.build_context_masks(context, receptors, times)

    daegyo_now = apartment_prisms(buildings, DAEGYO_JIBUN, "대교 기존")
    hwarang_now = apartment_prisms(buildings, HWARANG_JIBUN, "화랑 기존")
    hwarang_plan = load_hwarang_plan(args.hwarang_plan)

    scens = [
        Scen("S0", "현황", daegyo_now, hwarang_now),
        Scen("S1", "대교 신축안", plan_prisms, hwarang_now),
        Scen("S2", "화랑 재건축만", daegyo_now, hwarang_plan),
        Scen("S3", "동시 재건축", plan_prisms, hwarang_plan),
    ]

    # ── 시나리오별 평가 → 단위(동/운동장)별 평균 일조시간 ──────────────────
    hours: dict[str, dict[str, float]] = {}
    cont: dict[str, dict[str, float]] = {}
    for scn in scens:
        res = H.evaluate(receptors, scn.prisms, times, masks, args.step_min)
        agg_t: dict[str, list[float]] = defaultdict(list)
        agg_c: dict[str, list[float]] = defaultdict(list)
        for r in res:
            agg_t[r["building"]].append(r["total_h_08_16"])
            agg_c[r["building"]].append(r["cont_h_08_16"])
        hours[scn.code] = {k: sum(v) / len(v) for k, v in agg_t.items()}
        cont[scn.code] = {k: sum(v) / len(v) for k, v in agg_c.items()}

    def table(kind: str) -> None:
        print(f"\n■ {kind}별 평균 일조시간(동지일 08~16시, 단위: 시간)")
        print(f"{'구분':<26}{'수광점':>6}{'S0현황':>8}{'S1대교':>8}{'S2화랑':>8}"
              f"{'S3동시':>8}{'S1−S0':>8}{'S3−S0':>8}")
        print("-" * 82)
        units = [u for u in unit_info if unit_kind[u] == kind]
        units.sort(key=lambda u: (unit_info[u]["jibun"], u))
        for u in units:
            h0, h1, h2, h3 = (hours[s.code][u] for s in scens)
            print(f"{u:<26}{unit_info[u]['n']:>6}{h0:>8.2f}{h1:>8.2f}{h2:>8.2f}"
                  f"{h3:>8.2f}{h1-h0:>+8.2f}{h3-h0:>+8.2f}")

    table("교사동")
    table("운동장")

    # ── 초점 학교: 시간대별 차폐 원인 분해 ────────────────────────────────
    focus_units = [u for u in unit_info if unit_info[u]["jibun"] == args.focus]
    focus_rec = [r for r in receptors if r.building in focus_units]
    focus_name = school_label(args.focus, groups[args.focus])
    print(f"\n■ {focus_name} – 시간대별 차폐 원인 (수광점 {len(focus_rec)}개 중 비율)")
    attrib_rows = []
    for scn in (scens[0], scens[3]):
        counts, sunny = attribution(
            focus_rec, times,
            {"화랑": scn.hwarang, "대교": scn.daegyo, "기타 기존건물": context})
        print(f"\n   [{scn.code} {scn.name}]")
        print(f"   {'시각':<7}{'일조':>8}{'화랑':>8}{'대교':>8}{'기타':>8}{'자기그늘':>9}")
        for t_index, (clock, _a, _z) in enumerate(times):
            if abs(clock - round(clock)) > 1e-6:
                continue
            n = len(focus_rec)
            row = {
                "scen": scn.code, "clock": f"{int(clock):02d}:00",
                "sun": sunny[t_index] / n * 100,
                "hwarang": counts["화랑"][t_index] / n * 100,
                "daegyo": counts["대교"][t_index] / n * 100,
                "other": counts["기타 기존건물"][t_index] / n * 100,
                "self": counts["자기그늘(창면방위)"][t_index] / n * 100,
            }
            attrib_rows.append(row)
            print(f"   {row['clock']:<7}{row['sun']:>7.0f}%{row['hwarang']:>7.0f}%"
                  f"{row['daegyo']:>7.0f}%{row['other']:>7.0f}%{row['self']:>8.0f}%")

    write_outputs(args, scens, hours, cont, unit_kind, unit_info, attrib_rows)
    return 0


def write_outputs(args, scens, hours, cont, unit_kind, unit_info, attrib_rows) -> None:
    out = args.outdir
    with (out / "daegyo_school_hours.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["구분", "종류", "지번", "층수", "수광점", "면적m2", "X", "Y"]
                   + [f"{s.code}_총일조h" for s in scens]
                   + [f"{s.code}_연속일조h" for s in scens]
                   + ["S1-S0_총h", "S2-S0_총h", "S3-S0_총h"])
        for u in sorted(unit_info, key=lambda u: (unit_info[u]["jibun"],
                                                  unit_kind[u], u)):
            i = unit_info[u]
            h = [hours[s.code][u] for s in scens]
            c = [cont[s.code][u] for s in scens]
            w.writerow([u, unit_kind[u], i["jibun"], i["floors"], i["n"], i["area"],
                        i["x"], i["y"]]
                       + [round(v, 2) for v in h] + [round(v, 2) for v in c]
                       + [round(h[1] - h[0], 2), round(h[2] - h[0], 2),
                          round(h[3] - h[0], 2)])

    with (out / "daegyo_school_attribution.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["시나리오", "시각", "일조%", "화랑차폐%", "대교차폐%",
                    "기타차폐%", "자기그늘%"])
        for r in attrib_rows:
            w.writerow([r["scen"], r["clock"], round(r["sun"]), round(r["hwarang"]),
                        round(r["daegyo"]), round(r["other"]), round(r["self"])])
    print(f"\n결과 저장: {out}/daegyo_school_hours.csv, daegyo_school_attribution.csv")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
