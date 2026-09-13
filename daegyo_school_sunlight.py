#!/usr/bin/env python3
"""대교아파트 재건축(사업시행인가)안의 주변 학교 일조영향 분석.

여의도동 41 대교아파트 기존 4개동(12층)을 철거하고 인가안(4개동·최고 48층,
저층부 5개)을 신축할 때, 반경 내 학교 교실 창면의 동지일 일조가 어떻게
변하는지 실제 태양궤적·그림자 기하로 정량 비교한다.

분석 구성
    ① 현황 vs 신축안 : 대교 재건축 단독 영향
    ② 2×2 시나리오   : 대교(현황/신축) × 화랑(현황/재건축) 누적영향과 상호작용
    ③ 동별 기여도    : 어느 동이 일조 저해의 주원인인지 (해당 동 제거 시 회복량)
    ④ 시간대 프로파일: 하루 중 언제 그림자가 드는지
    ⑤ 층수 민감도    : 주원인 동의 층수를 낮출 때의 회복량

판정 기준
    기준A(교육환경평가 일반) 동지일 08~16시 연속 2시간 이상 또는 총 4시간 이상
    기준B(강화·고등학교)     동지일 09~15시 연속 2시간 이상
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

import hwarang_massing_study as H

DAEGYO_JIBUN = "41"
HWARANG_JIBUN = "40-4"
APT_FLOOR_H = H.USE_FLOOR_HEIGHT["공동주택"]     # 기존 구축 아파트 층고 2.8m
APT_ROOF_M = 2.0                                  # 기존 아파트 옥탑/파라펫

DEFAULT_PLAN = Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson")
DEFAULT_HWARANG_PLAN = Path("outputs/hwarang_final/hwarang_final_massing.geojson")
DEFAULT_OUTDIR = Path("outputs/daegyo_school")

# 저층부/타워 구분 높이(m) – 인가안 저층부는 최고 9m
TOWER_MIN_H = 50.0


# --------------------------------------------------------------------------- #
# 입력
# --------------------------------------------------------------------------- #
def load_named_buildings(path: Path) -> list[dict[str, Any]]:
    """AL_D010을 읽되 건물명(A24)까지 가져온다(학교명 식별용)."""
    conn = sqlite3.connect(path)
    table = conn.execute(
        "select table_name from gpkg_contents where data_type='features'"
    ).fetchone()[0]
    rows: list[dict[str, Any]] = []
    for blob, jibun, use, name, dong, floors, area in conn.execute(
        f'select geom, A5, A9, A24, A25, A26, A12 from "{table}"'
    ):
        if blob is None:
            continue
        geom = H.read_gpkg_geom(blob)
        if geom.is_empty or geom.area < 1.0:
            continue
        rows.append({
            "geom": geom,
            "jibun": jibun or "",
            "use": use or "",
            "name": (name or "").strip(),
            "dong": (dong or "").strip(),
            "floors": int(floors or 0),
            "build_area": float(area or 0.0),
        })
    conn.close()
    return rows


def load_hwarang_plan(path: Path) -> list[H.Prism]:
    """화랑 확정 매싱의 외형선(발코니 끝 = 실제 그림자 형상)을 읽는다."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    outline = height = None
    for feature in data["features"]:
        props = feature["properties"]
        if props.get("height_m"):
            height = float(props["height_m"])
        if str(props.get("line", "")).startswith("외형선"):
            outline = Polygon(feature["geometry"]["coordinates"][0])
    if outline is None or height is None:
        return []
    return [H.Prism(outline, height, "화랑 재건축 55F")]


def apartment_prisms(buildings: Sequence[dict[str, Any]], jibun: str,
                     tag: str) -> list[H.Prism]:
    prisms = []
    for row in buildings:
        if row["jibun"] != jibun or row["floors"] <= 0:
            continue
        top = row["floors"] * APT_FLOOR_H + APT_ROOF_M
        prisms.append(H.Prism(row["geom"], top,
                              f"{tag} {row['dong'] or '동'}({row['floors']}F)"))
    return prisms


def school_label(jibun: str, rows: Sequence[dict[str, Any]]) -> str:
    """지번 + 대표 학교명.

    한 지번에 교사동과 부속시설(예: 정보화센타)이 섞여 있으므로
    '학교'가 들어간 이름을 우선하고, 없으면 연면적이 큰 이름을 택한다.
    """
    area: dict[str, float] = defaultdict(float)
    for row in rows:
        if row["name"]:
            area[row["name"]] += row["build_area"] or row["geom"].area
    if not area:
        return f"{jibun} (학교명 미상)"
    best = max(area, key=lambda n: ("학교" in n, area[n]))
    return f"{jibun} {best}"


def dong_of(prism: H.Prism) -> str:
    """'대교 102-B' → '102동', '대교 P-S1' → '저층부'."""
    tail = prism.label.split()[-1]
    if tail.startswith("P-"):
        return "저층부"
    return f"{tail.split('-')[0]}동"


# --------------------------------------------------------------------------- #
@dataclass
class Scenario:
    code: str
    name: str
    prisms: list[H.Prism] = field(default_factory=list)
    note: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    school: dict[str, float] = field(default_factory=dict)
    school_b: dict[str, float] = field(default_factory=dict)
    results: list[dict[str, Any]] = field(default_factory=list)


def per_school_strict(results: Sequence[dict[str, Any]]) -> dict[str, float]:
    """학교별 기준B(09~15시 연속 2시간) 충족률."""
    schools = sorted({r["building"] for r in results})
    return {
        j: H.summarize([r for r in results if r["building"] == j])["pass_strict_pct"]
        for j in schools
    }


def run(scn: Scenario, receptors, times, masks, step_min: int) -> Scenario:
    scn.results = H.evaluate(receptors, scn.prisms, times, masks, step_min)
    scn.stats = H.summarize(scn.results)
    scn.school = H.per_school(scn.results)
    scn.school_b = per_school_strict(scn.results)
    return scn


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="대교아파트 재건축안 학교 일조영향 분석")
    p.add_argument("--buildings", type=Path, required=True, help="AL_D010 GeoPackage")
    p.add_argument("--plan", type=Path, default=DEFAULT_PLAN,
                   help="대교 신축 폴리곤(GeoJSON 또는 GPKG)")
    p.add_argument("--hwarang-plan", type=Path, default=DEFAULT_HWARANG_PLAN,
                   help="화랑 확정 매싱 GeoJSON(누적 시나리오용)")
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument("--date", type=str, default="12-22", help="분석 기준일 MM-DD(동지)")
    p.add_argument("--school-radius", type=float, default=350.0, help="검토 학교 반경(m)")
    p.add_argument("--context-radius", type=float, default=500.0, help="주변 차폐물 반경(m)")
    p.add_argument("--step-min", type=int, default=10, help="시간 간격(분)")
    p.add_argument("--sensitivity", type=int, nargs="*",
                   default=[5, 10, 15, 20, 25, 30, 35],
                   help="주원인 동 층수 저감 민감도(감소 층수)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    from pyproj import CRS, Transformer

    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    plan_prisms = H.load_daegyo(args.plan)
    site_union = unary_union([p.footprint for p in plan_prisms])
    centre = site_union.centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)

    # ── 학교·수광점 ────────────────────────────────────────────────────────
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in buildings:
        if (row["use"] == H.SCHOOL_USE and row["floors"] > 0
                and row["geom"].centroid.distance(centre) <= args.school_radius):
            groups[row["jibun"]].append(row)
    if not groups:
        raise SystemExit("반경 내 학교(교육연구시설)를 찾지 못했습니다.")

    labels = {j: school_label(j, rows) for j, rows in groups.items()}
    school_rows: list[dict[str, Any]] = []
    for jibun, rows in groups.items():
        for row in rows:
            school_rows.append({**row, "jibun": labels[jibun]})
    receptors = H.make_receptors(school_rows)

    print(f"대교 신축안 중심 E{centre.x:,.1f} / N{centre.y:,.1f}  "
          f"(건축면적합 {site_union.area:,.0f}㎡)")
    print(f"분석 기준일 {args.date}(동지) · 시간간격 {args.step_min}분 · "
          f"수광점 {len(receptors):,}개\n")
    print("■ 검토 대상 학교")
    for jibun, rows in sorted(groups.items(),
                              key=lambda kv: unary_union(
                                  [r["geom"] for r in kv[1]]).distance(site_union)):
        geom = unary_union([r["geom"] for r in rows])
        towers = [p for p in plan_prisms if p.top_m >= TOWER_MIN_H]
        dist_low = min(p.footprint.distance(geom) for p in plan_prisms)
        dist_tower = min(p.footprint.distance(geom) for p in towers)
        n_rec = sum(1 for r in receptors if r.building == labels[jibun])
        print(f"   {labels[jibun]:<22} 동수 {len(rows)}  최고 {max(r['floors'] for r in rows)}층  "
              f"대지최단 {dist_low:5.1f}m  타워최단 {dist_tower:5.1f}m  수광점 {n_rec:3d}")

    # ── 차폐물: 대교·화랑 대지 밖의 기존 건물(시나리오 무관) ────────────────
    context: list[H.Prism] = []
    for row in buildings:
        if row["jibun"] in (DAEGYO_JIBUN, HWARANG_JIBUN) or row["floors"] <= 0:
            continue
        if row["geom"].centroid.distance(centre) > args.context_radius:
            continue
        geom = row["geom"]
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            context.append(H.Prism(poly, H.building_height(row),
                                   f"기존 {row['jibun']} {row['name'] or row['dong']}".strip()))
    print(f"\n주변 고정 차폐물 {len(context)}개(반경 {args.context_radius:.0f}m, "
          f"대교·화랑 대지 제외)")

    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)
    masks = H.build_context_masks(context, receptors, times)

    daegyo_now = apartment_prisms(buildings, DAEGYO_JIBUN, "대교 기존")
    hwarang_now = apartment_prisms(buildings, HWARANG_JIBUN, "화랑 기존")
    hwarang_plan = load_hwarang_plan(args.hwarang_plan)
    print(f"대교 기존 {len(daegyo_now)}개동(12층, {max(p.top_m for p in daegyo_now):.1f}m) → "
          f"신축 {len(plan_prisms)}개(최고 {max(p.top_m for p in plan_prisms):.1f}m)")

    # ── ①② 2×2 시나리오 ──────────────────────────────────────────────────
    scenarios = [
        Scenario("S0", "현황(대교 기존·화랑 기존)", daegyo_now + hwarang_now, "기준선"),
        Scenario("S1", "대교 신축안(화랑 기존)", plan_prisms + hwarang_now,
                 "대교 재건축 단독 영향"),
        Scenario("S2", "대교 기존 + 화랑 재건축", daegyo_now + hwarang_plan,
                 "화랑 재건축 단독 영향(참고)"),
        Scenario("S3", "대교 신축안 + 화랑 재건축", plan_prisms + hwarang_plan,
                 "누적(동시 재건축)"),
    ]
    if not hwarang_plan:
        scenarios = scenarios[:2]
        print("   (화랑 확정 매싱 파일이 없어 누적 시나리오는 생략합니다)")

    print("\n■ 시나리오별 학교 일조 충족률")
    print(f"{'코드':<4}{'시나리오':<28}{'기준A':>8}{'기준B':>8}{'평균연속h':>10}"
          f"{'평균총h':>9}{'불충족점':>9}")
    print("-" * 78)
    for scn in scenarios:
        run(scn, receptors, times, masks, args.step_min)
        s = scn.stats
        print(f"{scn.code:<4}{scn.name:<28}{s['pass_pct']:>7.1f}%{s['pass_strict_pct']:>7.1f}%"
              f"{s['mean_cont_h']:>10.2f}{s['mean_total_h']:>9.2f}{s['n_fail']:>9d}")

    base, plan_scn = scenarios[0], scenarios[1]
    print(f"\n■ 대교 재건축 단독 영향 (S1 − S0)")
    print(f"   기준A {base.stats['pass_pct']:.1f}% → {plan_scn.stats['pass_pct']:.1f}% "
          f"({plan_scn.stats['pass_pct']-base.stats['pass_pct']:+.1f}%p)")
    print(f"   기준B {base.stats['pass_strict_pct']:.1f}% → "
          f"{plan_scn.stats['pass_strict_pct']:.1f}% "
          f"({plan_scn.stats['pass_strict_pct']-base.stats['pass_strict_pct']:+.1f}%p)")

    order = sorted(plan_scn.school, key=lambda j: plan_scn.school[j] - base.school[j])
    print(f"\n■ 학교별 시나리오 충족률 (기준A/기준B)")
    header = f"{'학교':<24}" + "".join(f"{s.code:>15}" for s in scenarios)
    print(header)
    print("-" * len(header))
    for j in order:
        cells = "".join(f"{s.school[j]:>7.1f}%/{s.school_b[j]:>5.1f}%" for s in scenarios)
        print(f"{j:<24}{cells}")

    print(f"\n■ 학교별 대교 재건축 영향 (S1 − S0)")
    for j in order:
        print(f"   {j:<22} 기준A {base.school[j]:5.1f}% → {plan_scn.school[j]:5.1f}% "
              f"({plan_scn.school[j]-base.school[j]:+5.1f}%p) | "
              f"기준B {base.school_b[j]:5.1f}% → {plan_scn.school_b[j]:5.1f}% "
              f"({plan_scn.school_b[j]-base.school_b[j]:+5.1f}%p)")

    worst = order[0]

    # 누적/상호작용 분해
    if len(scenarios) == 4:
        s0, s1, s2, s3 = (s.stats["pass_pct"] for s in scenarios)
        print(f"\n■ 누적영향 분해 (기준A, 전체 수광점)")
        print(f"   대교 단독  S1−S0 = {s1-s0:+.1f}%p")
        print(f"   화랑 단독  S2−S0 = {s2-s0:+.1f}%p")
        print(f"   동시 재건축 S3−S0 = {s3-s0:+.1f}%p")
        print(f"   상호작용   S3−S0−(S1−S0)−(S2−S0) = {(s3-s0)-(s1-s0)-(s2-s0):+.1f}%p"
              f"   (양수면 그림자 중첩으로 합보다 완화)")

        # 기존 화랑 보고서 수치와의 정합성 교차검증
        print(f"\n■ 기존 화랑 보고서와의 정합성 교차검증")
        print(f"   화랑 보고서 '현황 82.6%'는 대교 신축을 이미 전제한 값 → 본 분석 S1 {s1:.1f}%")
        print(f"   화랑 보고서 '확정 55층 85.9%'                     → 본 분석 S3 {s3:.1f}%")
        print(f"   (수광점 집합이 대교/화랑 각 중심 반경으로 달라 ±0.5%p 차이는 정상)")

    # ── ③ 동별 기여도 ─────────────────────────────────────────────────────
    print(f"\n■ 동별 기여도 — 해당 동만 제거했을 때 회복량 (S1 기준)")
    by_dong: dict[str, list[H.Prism]] = defaultdict(list)
    for p in plan_prisms:
        by_dong[dong_of(p)].append(p)
    contrib: list[tuple[str, float, float, float]] = []
    for dong, prisms in sorted(by_dong.items()):
        removed = {id(p) for p in prisms}
        kept = [p for p in plan_scn.prisms if id(p) not in removed]
        res = H.evaluate(receptors, kept, times, masks, args.step_min)
        stats, school = H.summarize(res), H.per_school(res)
        contrib.append((dong, stats["pass_pct"] - plan_scn.stats["pass_pct"],
                        school[worst] - plan_scn.school[worst],
                        max(p.top_m for p in prisms)))
    contrib.sort(key=lambda r: -r[1])
    print(f"{'동':<10}{'최고높이':>9}{'전체회복':>10}{'최악학교회복':>13}")
    print("-" * 44)
    for dong, d_all, d_worst, top in contrib:
        print(f"{dong:<10}{top:>8.1f}m{d_all:>+9.1f}%p{d_worst:>+12.1f}%p")

    # ── ④ 시간대 프로파일 (최악 학교) ──────────────────────────────────────
    idx_worst = [i for i, r in enumerate(receptors) if r.building == worst]
    flags_base = H.sunlit_flags([receptors[i] for i in idx_worst],
                                base.prisms, times, masks)
    flags_plan = H.sunlit_flags([receptors[i] for i in idx_worst],
                                plan_scn.prisms, times, masks)
    print(f"\n■ 시간대별 일조 수광점 비율 — {worst}")
    print(f"{'시각':<8}{'현황':>8}{'신축안':>8}{'변화':>8}")
    print("-" * 32)
    profile = []
    for t_index, (clock, altitude, azimuth) in enumerate(times):
        if abs(clock - round(clock)) > 1e-6:
            continue
        nb = sum(1 for row in flags_base if row[t_index]) / len(idx_worst) * 100
        np_ = sum(1 for row in flags_plan if row[t_index]) / len(idx_worst) * 100
        profile.append((clock, altitude, azimuth, nb, np_))
        print(f"{int(clock):02d}:00  {nb:>7.1f}%{np_:>8.1f}%{np_-nb:>+8.1f}%p")

    # ── ⑤ 층수 민감도 (주원인 동) ─────────────────────────────────────────
    lead = contrib[0][0] if contrib else None
    sens: list[tuple[int, float, float]] = []
    if lead and lead != "저층부" and args.sensitivity:
        print(f"\n■ 층수 민감도 — 주원인 {lead} 층수 저감 시 (연면적 보전 없음, 순수 민감도)")
        print(f"{'저감':<8}{'전체기준A':>11}{'최악학교':>11}")
        print("-" * 32)
        for cut in args.sensitivity:
            drop = cut * H.RESI_FLOOR_H
            mod = [H.Prism(p.footprint, max(p.top_m - drop, 10.0), p.label)
                   if dong_of(p) == lead else p for p in plan_scn.prisms]
            res = H.evaluate(receptors, mod, times, masks, args.step_min)
            stats, school = H.summarize(res), H.per_school(res)
            sens.append((cut, stats["pass_pct"] - plan_scn.stats["pass_pct"],
                         school[worst] - plan_scn.school[worst]))
            print(f"−{cut}층   {stats['pass_pct']:>9.1f}%"
                  f"({stats['pass_pct']-plan_scn.stats['pass_pct']:+.1f}p)"
                  f"{school[worst]:>8.1f}%({school[worst]-plan_scn.school[worst]:+.1f}p)")

    write_outputs(args, scenarios, receptors, groups, labels, plan_prisms,
                  daegyo_now, contrib, profile, sens, worst, site_union)
    return 0


# --------------------------------------------------------------------------- #
def write_outputs(args, scenarios, receptors, groups, labels, plan_prisms,
                  daegyo_now, contrib, profile, sens, worst, site_union) -> None:
    out = args.outdir
    base, plan_scn = scenarios[0], scenarios[1]

    # 시나리오 요약
    with (out / "daegyo_school_summary.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        schools = sorted(base.school)
        w.writerow(["코드", "시나리오", "설명", "기준A%", "기준B%", "평균연속h",
                    "평균총h", "불충족수광점", "전체수광점"]
                   + [f"{j} 기준A%" for j in schools]
                   + [f"{j} 기준B%" for j in schools])
        for s in scenarios:
            w.writerow([s.code, s.name, s.note,
                        round(s.stats["pass_pct"], 1),
                        round(s.stats["pass_strict_pct"], 1),
                        round(s.stats["mean_cont_h"], 2),
                        round(s.stats["mean_total_h"], 2),
                        s.stats["n_fail"], s.stats["n"]]
                       + [round(s.school[j], 1) for j in schools]
                       + [round(s.school_b[j], 1) for j in schools])

    # 수광점 상세(현황 vs 신축안)
    with (out / "daegyo_school_receptors.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["학교", "층", "X", "Y", "Z", "창면방위",
                    "현황_연속h", "현황_총h", "현황_충족",
                    "신축_연속h", "신축_총h", "신축_충족", "총h변화"])
        for rb, rp in zip(base.results, plan_scn.results):
            ok_b = rb["cont_h_08_16"] >= 2.0 or rb["total_h_08_16"] >= 4.0
            ok_p = rp["cont_h_08_16"] >= 2.0 or rp["total_h_08_16"] >= 4.0
            w.writerow([rb["building"], rb["floor"], rb["x"], rb["y"], rb["z"],
                        rb["normal_az"], rb["cont_h_08_16"], rb["total_h_08_16"],
                        "충족" if ok_b else "불충족",
                        rp["cont_h_08_16"], rp["total_h_08_16"],
                        "충족" if ok_p else "불충족",
                        round(rp["total_h_08_16"] - rb["total_h_08_16"], 2)])

    # 학교별 층별
    with (out / "daegyo_school_by_floor.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["학교", "층", "수광점", "현황_기준A%", "신축_기준A%", "변화%p",
                    "현황_평균총h", "신축_평균총h"])
        keys = sorted({(r["building"], r["floor"]) for r in base.results})
        for school, floor in keys:
            rb = [r for r in base.results
                  if r["building"] == school and r["floor"] == floor]
            rp = [r for r in plan_scn.results
                  if r["building"] == school and r["floor"] == floor]
            sb, sp = H.summarize(rb), H.summarize(rp)
            w.writerow([school, floor, len(rb), round(sb["pass_pct"], 1),
                        round(sp["pass_pct"], 1),
                        round(sp["pass_pct"] - sb["pass_pct"], 1),
                        round(sb["mean_total_h"], 2), round(sp["mean_total_h"], 2)])

    # 동별 기여도 / 시간대 / 민감도
    with (out / "daegyo_school_contribution.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["동", "최고높이m", "제거시_전체회복%p", f"제거시_{worst}_회복%p"])
        for dong, d_all, d_worst, top in contrib:
            w.writerow([dong, round(top, 1), round(d_all, 1), round(d_worst, 1)])
        w.writerow([])
        w.writerow(["시각", "태양고도", "태양방위", f"{worst}_현황일조%", "신축일조%", "변화%p"])
        for clock, alt, az, nb, np_ in profile:
            w.writerow([f"{int(clock):02d}:00", round(alt, 1), round(az, 1),
                        round(nb, 1), round(np_, 1), round(np_ - nb, 1)])
        if sens:
            w.writerow([])
            w.writerow(["층수저감", "전체기준A변화%p", f"{worst}_변화%p"])
            for cut, d_all, d_worst in sens:
                w.writerow([f"-{cut}층", round(d_all, 1), round(d_worst, 1)])

    # 수광점 GeoJSON(QGIS 매핑용)
    feats = []
    for rb, rp in zip(base.results, plan_scn.results):
        ok_b = rb["cont_h_08_16"] >= 2.0 or rb["total_h_08_16"] >= 4.0
        ok_p = rp["cont_h_08_16"] >= 2.0 or rp["total_h_08_16"] >= 4.0
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [rb["x"], rb["y"]]},
            "properties": {
                "school": rb["building"], "floor": rb["floor"], "z_m": rb["z"],
                "now_total_h": rb["total_h_08_16"], "now_cont_h": rb["cont_h_08_16"],
                "now_pass": ok_b,
                "plan_total_h": rp["total_h_08_16"], "plan_cont_h": rp["cont_h_08_16"],
                "plan_pass": ok_p,
                "delta_total_h": round(rp["total_h_08_16"] - rb["total_h_08_16"], 2),
                "status": ("유지-충족" if ok_b and ok_p else
                           "신규불충족" if ok_b and not ok_p else
                           "유지-불충족" if not ok_b and not ok_p else "개선-충족"),
            },
        })
    (out / "daegyo_school_receptors.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
        "features": feats,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    export_plan_svg(out / "daegyo_school_shadow.svg", plan_prisms, daegyo_now,
                    groups, labels, plan_scn, base, args)
    print(f"\n결과 저장: {out}")


def export_plan_svg(path: Path, plan_prisms, daegyo_now, groups, labels,
                    plan_scn, base, args) -> None:
    """배치 평면 + 시각별 그림자 + 학교별 충족률 표기."""
    from pyproj import CRS, Transformer
    month, day = (int(v) for v in args.date.split("-"))
    towers = [p for p in plan_prisms if p.top_m >= TOWER_MIN_H]
    low = [p for p in plan_prisms if p.top_m < TOWER_MIN_H]
    site = unary_union([p.footprint for p in plan_prisms])
    centre = site.centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)

    shadow_sets = []
    for target, colour in ((10.0, "#f4a261"), (12.5, "#e76f51"), (14.0, "#8ab17d")):
        clock, altitude, azimuth = min(times, key=lambda t: abs(t[0] - target))
        polys = [H.shadow_polygon(p, H.SCHOOL_WINDOW_H, azimuth, altitude)
                 for p in plan_prisms]
        polys = [q for q in polys if q is not None and not q.is_empty]
        if polys:
            shadow_sets.append((unary_union(polys), colour,
                                f"{int(clock):02d}:{int(round((clock % 1) * 60)):02d}"))

    school_geoms = {j: unary_union([r["geom"] for r in rows])
                    for j, rows in groups.items()}
    layers = [site, *(g for g, _c, _l in shadow_sets), *school_geoms.values(),
              *(p.footprint for p in daegyo_now)]
    minx, miny, maxx, maxy = unary_union(layers).buffer(25.0).bounds
    px_w = 1080.0
    scale = px_w / (maxx - minx)
    px_h = (maxy - miny) * scale

    def path_of(geom) -> str:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return "".join(
            '<polygon points="' + " ".join(
                f"{(x - minx) * scale:.1f},{(maxy - y) * scale:.1f}"
                for x, y in poly.exterior.coords) + '" />' for poly in polys)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h + 96:.0f}" viewBox="0 0 {px_w:.0f} {px_h + 96:.0f}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>',
           '<text x="14" y="26" font-family="sans-serif" font-size="17" '
           'font-weight="bold" fill="#111">대교아파트 재건축안 – 동지일 그림자와 '
           '학교 일조 (상단이 북)</text>',
           f'<text x="14" y="48" font-family="sans-serif" font-size="12.5" fill="#555">'
           f'전체 기준A 충족률 현황 {base.stats["pass_pct"]:.1f}% → '
           f'신축안 {plan_scn.stats["pass_pct"]:.1f}% '
           f'({plan_scn.stats["pass_pct"]-base.stats["pass_pct"]:+.1f}%p) · '
           f'그림자는 창면높이 1.2m 기준</text>',
           f'<g transform="translate(0,64)">']
    for geom, colour, label in shadow_sets:
        svg.append(f'<g fill="{colour}" fill-opacity="0.30" stroke="none">'
                   f'{path_of(geom)}</g>')
    for prism in daegyo_now:
        svg.append('<g fill="none" stroke="#8d99ae" stroke-width="1.6" '
                   f'stroke-dasharray="6 4">{path_of(prism.footprint)}</g>')
    for jibun, geom in school_geoms.items():
        label = labels[jibun]
        delta = plan_scn.school[label] - base.school[label]
        colour = "#c1121f" if delta <= -5 else "#e09f3e" if delta < -0.5 else "#2a6f97"
        svg.append(f'<g fill="{colour}" fill-opacity="0.8" stroke="#14425c" '
                   f'stroke-width="1.2">{path_of(geom)}</g>')
        cx = (geom.centroid.x - minx) * scale
        cy = (maxy - geom.centroid.y) * scale
        svg.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="sans-serif" '
                   f'font-size="12" font-weight="bold" text-anchor="middle" '
                   f'fill="#fff" stroke="#000" stroke-width="0.4">{label}</text>')
        svg.append(f'<text x="{cx:.1f}" y="{cy + 15:.1f}" font-family="sans-serif" '
                   f'font-size="11.5" text-anchor="middle" fill="#111">'
                   f'{base.school[label]:.1f}% → {plan_scn.school[label]:.1f}% '
                   f'({delta:+.1f}%p)</text>')
    for prism in low:
        svg.append('<g fill="#adb5bd" fill-opacity="0.85" stroke="#495057" '
                   f'stroke-width="1">{path_of(prism.footprint)}</g>')
    for prism in towers:
        svg.append('<g fill="#1d3557" fill-opacity="0.92" stroke="#0b1c30" '
                   f'stroke-width="1.4">{path_of(prism.footprint)}</g>')
        cen = prism.footprint.centroid
        svg.append(f'<text x="{(cen.x - minx) * scale:.1f}" '
                   f'y="{(maxy - cen.y) * scale:.1f}" font-family="sans-serif" '
                   f'font-size="9.5" text-anchor="middle" fill="#fff">'
                   f'{prism.label.split()[-1]}</text>')
    svg.append("</g>")
    legend = [("#f4a261", "10:00 그림자"), ("#e76f51", "12:30 그림자"),
              ("#8ab17d", "14:00 그림자"), ("#1d3557", "신축 타워"),
              ("#adb5bd", "신축 저층부"), ("#2a6f97", "학교")]
    for i, (colour, text) in enumerate(legend):
        x = 14 + i * 178
        svg.append(f'<rect x="{x}" y="{px_h + 70:.0f}" width="13" height="13" '
                   f'fill="{colour}" fill-opacity="0.75" stroke="#333" stroke-width="0.6"/>')
        svg.append(f'<text x="{x + 19}" y="{px_h + 81:.0f}" font-family="sans-serif" '
                   f'font-size="11.5" fill="#111">{text}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
