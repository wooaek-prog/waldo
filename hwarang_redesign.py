#!/usr/bin/env python3
"""화랑아파트 재건축 – 학교 일조영향 최소화 설계안 재도출(보정 데이터 기준).

대교아파트 재건축안이 이미 지어진 상태를 전제로, 화랑아파트(여의도동 40-4)를
준주거 용적률 400% / 건폐율 60% 안에서 어떻게 지어야 주변 4개 학교의 일조
피해가 가장 작은지 탐색한다.

수광점은 **보정된 학교 건물 집합**(교사동 12개동 + 운동장 4개소)을 쓴다.
AL_D010에 용도·층수가 비어 있어 종전 분석에서 누락됐던 교사동(여의도여고
19.95m동, 여의도초 17.0m동 등)을 A16 높이로 복구한 자료다.

설계 우선순위(사용자 지정)
    1. 일조권 충족        기준A 충족률 최대
    2. 층수 고층화        층수 최대
    3. 최대 용적률        400% 적용
    4. 한강뷰 최대화      북측 한강 조망 개방률 최대
    불충족 시            불충족 수광점의 평균 일조시간 최대 확보

용적률 400%를 고정하면 '판 면적 × 층수 = 연면적'이므로 ②와 ③은
**판을 작게 하고 층수를 올리는 것**으로 동시에 달성된다. 다만 높아질수록
그림자가 길어져 ①과 상충하므로, 그 절충점을 시뮬레이션으로 찾는다.
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

from shapely.affinity import rotate, translate
from shapely.geometry import LineString, Polygon, box, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree

import hwarang_massing_study as H
from daegyo_school_sunlight import (
    DAEGYO_JIBUN, HWARANG_JIBUN,
    load_named_buildings, resolved_height, school_label, school_parcel_rows,
)
from daegyo_school_hours import (
    building_receptors, dong_labels, ground_receptors, playground_area,
)

RESI_FLOOR_H = H.RESI_FLOOR_H
ROOFTOP_M = H.ROOFTOP_M


# --------------------------------------------------------------------------- #
# 발코니(서비스면적) – 외벽에서 1.5m. 연면적에서는 제외되지만
# 그림자를 만드는 실제 외형선은 발코니 끝이고, 건축면적은 발코니 끝에서
# 1m 후퇴한 선까지 산입한다(건축법 시행령 제119조 제1항 제2호).
BALCONY_M = 1.5
COVERAGE_INSET_M = 1.0


@dataclass
class Design:
    """화랑 설계안 후보.

    세 개의 선을 구분한다.
        plate    연면적선(내부 골조) – 용적률 산정 기준
        outline  외형선(발코니 끝)   – 실제 그림자·이격 판정 기준
        coverage 건축면적선(발코니 끝 −1m) – 건폐율 산정 기준
    """
    n_towers: int
    floors: int
    aspect: float               # 장변:단변 (연면적선 기준)
    azimuth: float              # 장변 방위각
    positions: list[tuple[float, float]]
    plate_m2: float             # 1개동 기준층 연면적선 면적
    prisms: list[H.Prism] = field(default_factory=list)
    balcony_m: float = BALCONY_M

    @property
    def height_m(self) -> float:
        return self.floors * RESI_FLOOR_H + ROOFTOP_M

    @property
    def gfa_m2(self) -> float:
        return self.plate_m2 * self.floors * self.n_towers

    @property
    def plate_dims(self) -> tuple[float, float]:
        short = math.sqrt(self.plate_m2 / self.aspect)
        return self.plate_m2 / short, short

    @property
    def outline_m2(self) -> float:
        lng, sht = self.plate_dims
        b = self.balcony_m
        return (lng + 2 * b) * (sht + 2 * b)

    @property
    def coverage_m2(self) -> float:
        lng, sht = self.plate_dims
        d = self.balcony_m - COVERAGE_INSET_M
        return (lng + 2 * d) * (sht + 2 * d)

    @property
    def footprint_m2(self) -> float:
        """건폐율 산정용 건축면적 합."""
        return self.coverage_m2 * self.n_towers

    @property
    def service_m2(self) -> float:
        """발코니(서비스) 면적 합 – 연면적 제외분."""
        return (self.outline_m2 - self.plate_m2) * self.floors * self.n_towers

    def label(self) -> str:
        return (f"{self.n_towers}개동·{self.floors}층·{self.aspect:g}:1·"
                f"방위{self.azimuth:g}°")


def rect_polygon(long_m: float, short_m: float, azimuth: float,
                 cx: float, cy: float) -> Polygon:
    """장변×단변 직사각형을 (cx,cy) 중심·장변 방위각으로 놓는다.

    로컬 +x가 방위각을 향하도록 (90°−방위) 회전한다.
    """
    rect = box(-long_m / 2, -short_m / 2, long_m / 2, short_m / 2)
    rect = rotate(rect, 90.0 - azimuth, origin=(0, 0))
    return translate(rect, cx, cy)


def plate_polygon(area: float, aspect: float, azimuth: float,
                  cx: float, cy: float, expand: float = 0.0) -> Polygon:
    """연면적선 기준 판. expand를 주면 사방으로 그만큼 키운다.

    expand=BALCONY_M      → 외형선(발코니 끝, 그림자·이격 판정)
    expand=BALCONY_M−1.0  → 건축면적선(건폐율 산정)
    """
    short = math.sqrt(area / aspect)
    long = area / short
    return rect_polygon(long + 2 * expand, short + 2 * expand, azimuth, cx, cy)


def outline_polygon(area: float, aspect: float, azimuth: float,
                    cx: float, cy: float, balcony: float = BALCONY_M) -> Polygon:
    """외형선(발코니 끝) – 실제로 그림자를 만드는 형상."""
    return plate_polygon(area, aspect, azimuth, cx, cy, balcony)


def build_design(n_towers: int, floors: int, aspect: float, azimuth: float,
                 positions: Sequence[tuple[float, float]], gfa: float,
                 balcony: float = BALCONY_M) -> Design:
    plate = gfa / (floors * n_towers)
    top = floors * RESI_FLOOR_H + ROOFTOP_M
    # 그림자는 발코니 끝(외형선)이 만든다
    prisms = [H.Prism(outline_polygon(plate, aspect, azimuth, x, y, balcony), top,
                      f"화랑 {i+1}동")
              for i, (x, y) in enumerate(positions)]
    return Design(n_towers, floors, aspect, azimuth, list(positions), plate,
                  prisms, balcony)


# --------------------------------------------------------------------------- #
# 한강 조망
# --------------------------------------------------------------------------- #
def river_azimuths(start: float, end: float, step: float = 10.0) -> list[float]:
    out, a = [], start % 360.0
    span = (end - start) % 360.0
    n = int(span // step) + 1
    for i in range(n):
        out.append((a + i * step) % 360.0)
    return out


class ViewModel:
    """북측 한강 조망 개방률(시선 광선 차폐 판정)."""

    def __init__(self, context: Sequence[H.Prism], azimuths: Sequence[float],
                 reach_m: float):
        self.prisms = [p for p in context if p.top_m > 0]
        self.tree = STRtree([p.footprint for p in self.prisms])
        self.azimuths = list(azimuths)
        self.reach = reach_m

    def openness(self, x: float, y: float, floors: int) -> float:
        """전 세대(층) 평균 조망 개방률(%)."""
        opened = 0
        for az in self.azimuths:
            dx = math.sin(math.radians(az)) * self.reach
            dy = math.cos(math.radians(az)) * self.reach
            ray = LineString([(x, y), (x + dx, y + dy)])
            blocked = 0.0
            for idx in self.tree.query(ray):
                p = self.prisms[idx]
                if p.footprint.intersects(ray):
                    blocked = max(blocked, p.top_m)
            first_open = math.ceil(blocked / RESI_FLOOR_H + 0.5)
            opened += max(0, floors - max(0, first_open - 1))
        return opened / (len(self.azimuths) * floors) * 100.0


# --------------------------------------------------------------------------- #
def stride_sample(receptors: Sequence[H.Receptor], every: int) -> list[H.Receptor]:
    """단위(building)별로 균등 간격 표본을 뽑는다(특정 층·동 편향 방지)."""
    by_unit: dict[str, list[H.Receptor]] = defaultdict(list)
    for r in receptors:
        by_unit[r.building].append(r)
    out: list[H.Receptor] = []
    for unit in sorted(by_unit):
        rows = by_unit[unit]
        out.extend(rows[::every] if len(rows) > every else rows[:1])
    return out


def metrics_of(results: Sequence[dict[str, Any]],
               horizontal: dict[str, bool]) -> dict[str, float]:
    """교사동(교실 창면)과 운동장을 분리해 집계한다.

    교육환경평가의 일조 기준은 교실을 대상으로 하므로 순위 판정은 교사동으로
    하고, 운동장은 함께 보고한다(운동장 격자점이 수광점의 70%라 섞으면
    교실 지표가 묻힌다).
    """
    cls = [r for r in results if not horizontal[r["building"]]]
    pg = [r for r in results if horizontal[r["building"]]]
    s = H.summarize(cls)
    fails = [r for r in cls
             if r["cont_h_08_16"] < 2.0 and r["total_h_08_16"] < 4.0]
    out = {
        "pass_pct": s["pass_pct"],
        "pass_strict_pct": s["pass_strict_pct"],
        "mean_total_h": s["mean_total_h"],
        "n_fail": len(fails),
        # 불충족 수광점이 실제로 확보하는 평균 일조시간(사용자 지정 차순위 기준)
        "fail_mean_h": (sum(r["total_h_08_16"] for r in fails) / len(fails))
                       if fails else 0.0,
    }
    if pg:
        sp = H.summarize(pg)
        out["pg_pass_pct"] = sp["pass_pct"]
        out["pg_mean_total_h"] = sp["mean_total_h"]
    else:
        out["pg_pass_pct"] = out["pg_mean_total_h"] = 0.0
    return out


def rank_key(m: dict[str, float], design: Design, view: float) -> tuple:
    """사용자 우선순위 lexicographic 정렬 키(작을수록 좋음).

    ① 교사동 충족률(0.1%p) → ② 불충족점 평균 일조시간 → ③ 층수 → ④ 한강조망
    용적률(③ 최대 용적률)은 모든 후보가 400%로 동일하므로 비교에서 빠진다.
    """
    return (
        -round(m["pass_pct"], 1),
        -round(m["fail_mean_h"], 2),
        -design.floors,
        -round(view, 1),
    )


def grid_centres(envelope: Polygon, area: float, aspect: float, azimuth: float,
                 step: float) -> list[tuple[float, float]]:
    """이격선 안쪽에 판이 완전히 들어가는 중심 좌표 격자."""
    minx, miny, maxx, maxy = envelope.bounds
    out: list[tuple[float, float]] = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if envelope.contains(outline_polygon(area, aspect, azimuth, x, y)):
                out.append((x, y))
            x += step
        y += step
    return out


# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="화랑아파트 설계안 재도출")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"),
                   help="대교 신축안(이미 지어진 것으로 전제)")
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_redesign"))
    p.add_argument("--site-area", type=float, default=9395.0)
    p.add_argument("--far", type=float, default=400.0)
    p.add_argument("--bcr", type=float, default=60.0)
    p.add_argument("--setback", type=float, default=3.0)
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--river-from", type=float, default=340.0)
    p.add_argument("--river-to", type=float, default=110.0)
    p.add_argument("--view-reach", type=float, default=600.0)
    p.add_argument("--balcony", type=float, default=BALCONY_M,
                   help="발코니(서비스) 깊이 m – 연면적 제외, 그림자는 발생")
    p.add_argument("--sample", type=int, default=3, help="스크리닝 표본 간격")
    p.add_argument("--top-n", type=int, default=8)
    p.add_argument("--timing", action="store_true", help="1회 평가 소요시간만 측정")
    # 탐색을 건너뛰고 지정한 설계안만 평가(시공 가능성까지 반영한 확정안 출력용)
    p.add_argument("--fix-floors", type=int)
    p.add_argument("--fix-aspect", type=float)
    p.add_argument("--fix-azimuth", type=float)
    p.add_argument("--fix-x", type=float)
    p.add_argument("--fix-y", type=float)
    return p.parse_args(argv)


def setup(args) -> dict[str, Any]:
    from pyproj import CRS, Transformer
    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    daegyo_new = H.load_daegyo(args.daegyo)
    site = H.build_site(buildings, args.site_area)
    centre = site.centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)
    all_b = unary_union([r["geom"] for r in buildings])

    # 학교 수광점(보정본): 교사동 창면 + 운동장 지반
    daegyo_centre = unary_union([p.footprint for p in daegyo_new]).centroid
    schools = school_parcel_rows(buildings, daegyo_centre, args.school_radius)
    receptors: list[H.Receptor] = []
    units: dict[str, dict[str, Any]] = {}
    for jibun, rows in sorted(schools.items()):
        school = school_label(jibun, rows).split(" ", 1)[1]
        for label, row in dong_labels([r for r in rows if r["classroom"]], school):
            rec = building_receptors([row], label)
            receptors += rec
            units[label] = {"kind": "교사동", "jibun": jibun, "school": school,
                            "geom": row["geom"], "n": len(rec)}
        sb = unary_union([r["geom"] for r in rows])
        pg = playground_area(sb, all_b, args.apron)
        label = f"{school} 운동장"
        rec = ground_receptors(pg, label)
        receptors += rec
        units[label] = {"kind": "운동장", "jibun": jibun, "school": school,
                        "geom": pg, "n": len(rec)}

    # 차폐물: 화랑 대지 밖 기존 건물 + 대교 신축안
    context: list[H.Prism] = []
    for row in buildings:
        if row["jibun"] == HWARANG_JIBUN:
            continue
        if row["geom"].centroid.distance(centre) > args.context_radius:
            continue
        height = resolved_height(row)
        if height <= 0.0:
            continue
        g = row["geom"]
        for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            context.append(H.Prism(poly, height, "기존건물"))
    context += daegyo_new

    existing_hwarang = [
        H.Prism(r["geom"], r["floors"] * H.USE_FLOOR_HEIGHT["공동주택"] + 2.0,
                f"화랑 기존 {r['dong']}")
        for r in buildings if r["jibun"] == HWARANG_JIBUN and r["floors"] > 0
    ]
    return {
        "buildings": buildings, "site": site, "centre": centre, "times": times,
        "receptors": receptors, "units": units, "context": context,
        "daegyo_new": daegyo_new, "existing": existing_hwarang,
        "envelope": site.buffer(-args.setback),
        "gfa": args.site_area * args.far / 100.0,
        "max_footprint": args.site_area * args.bcr / 100.0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import time
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    ctx = setup(args)
    site, envelope = ctx["site"], ctx["envelope"]
    receptors, times, context = ctx["receptors"], ctx["times"], ctx["context"]
    gfa = ctx["gfa"]

    print(f"화랑 대지 {site.area:,.0f}㎡ · 이격 {args.setback:.1f}m 후 "
          f"배치가능 {envelope.area:,.0f}㎡")
    print(f"용적률 {args.far:g}% → 연면적 {gfa:,.0f}㎡ / "
          f"건폐율 {args.bcr:g}% → 최대 건축면적 {ctx['max_footprint']:,.0f}㎡")
    print(f"학교 수광점 {len(receptors):,}개(교사동 "
          f"{sum(1 for r in receptors if not r.horizontal):,} / 운동장 "
          f"{sum(1 for r in receptors if r.horizontal):,}) · "
          f"차폐물 {len(context)}개(대교 신축안 포함)\n")

    t0 = time.time()
    masks_full = H.build_context_masks(context, receptors, times)
    print(f"전체 차폐 마스크 준비 {time.time()-t0:.1f}s")

    sample = stride_sample(receptors, args.sample)
    t0 = time.time()
    masks_s = H.build_context_masks(context, sample, times)
    print(f"표본({len(sample):,}개) 마스크 준비 {time.time()-t0:.1f}s")

    base = build_design(1, 55, 2.0, 345.0, [(ctx["centre"].x, ctx["centre"].y)], gfa)
    t0 = time.time()
    H.evaluate(sample, base.prisms, times, masks_s, args.step_min)
    t_s = time.time() - t0
    t0 = time.time()
    H.evaluate(receptors, base.prisms, times, masks_full, args.step_min)
    t_f = time.time() - t0
    print(f"1회 평가: 표본 {t_s:.2f}s / 전체 {t_f:.2f}s")
    if args.timing:
        return 0

    horiz = {label: info["kind"] == "운동장" for label, info in ctx["units"].items()}
    view = ViewModel(context, river_azimuths(args.river_from, args.river_to),
                     args.view_reach)

    def screen(design: Design) -> tuple[dict[str, float], float]:
        m = metrics_of(H.evaluate(sample, design.prisms, times, masks_s,
                                  args.step_min), horiz)
        v = sum(view.openness(x, y, design.floors)
                for x, y in design.positions) / design.n_towers
        return m, v

    def full(design: Design) -> tuple[dict[str, float], float]:
        m = metrics_of(H.evaluate(receptors, design.prisms, times, masks_full,
                                  args.step_min), horiz)
        v = sum(view.openness(x, y, design.floors)
                for x, y in design.positions) / design.n_towers
        return m, v

    # 기준선 – 현황(화랑 기존 3개동) / 대교만 신축된 상태
    base_now = metrics_of(H.evaluate(receptors, ctx["existing"], times,
                                     masks_full, args.step_min), horiz)
    base_none = metrics_of(H.evaluate(receptors, [], times, masks_full,
                                      args.step_min), horiz)
    print(f"\n기준선(교사동 기준A): 화랑 없음 {base_none['pass_pct']:.1f}% / "
          f"화랑 기존 10층 3개동 {base_now['pass_pct']:.1f}%")
    print(f"   (화랑 기존동이 학교에서 빼앗는 몫 = "
          f"{base_none['pass_pct']-base_now['pass_pct']:+.1f}%p, "
          f"교실 평균 {base_none['mean_total_h']-base_now['mean_total_h']:+.2f}h)\n")

    # 지정 설계안만 평가하는 경로(탐색 생략)
    if args.fix_floors:
        d = build_design(1, args.fix_floors, args.fix_aspect, args.fix_azimuth,
                         [(args.fix_x, args.fix_y)], gfa)
        m, v = full(d)
        best = {"design": d, "m": m, "view": v, "key": rank_key(m, d, v)}
        print(f"지정 설계안 평가: {d.label()}")
        report(args, ctx, best, [best], base_now, base_none, view)
        return 0

    results: list[dict[str, Any]] = []

    def record(design: Design, m: dict[str, float], v: float, stage: str) -> dict:
        row = {"design": design, "m": m, "view": v, "stage": stage,
               "key": rank_key(m, design, v)}
        results.append(row)
        return row

    # ── 1단계: 위치 ─────────────────────────────────────────────────────
    F0, A0, AZ0 = 55, 2.0, 345.0
    plate0 = gfa / F0
    cents = grid_centres(envelope, plate0, A0, AZ0, 5.0)
    print(f"1단계 위치 탐색: {len(cents)}개 지점 ({F0}층·{A0:g}:1·방위{AZ0:g}°)")
    pos_rows = []
    for cx, cy in cents:
        d = build_design(1, F0, A0, AZ0, [(cx, cy)], gfa)
        m, v = screen(d)
        pos_rows.append(record(d, m, v, "1-위치"))
    pos_rows.sort(key=lambda r: r["key"])
    for r in pos_rows[:3]:
        print(f"   {r['m']['pass_pct']:5.1f}%  불충족평균 {r['m']['fail_mean_h']:.2f}h  "
              f"조망 {r['view']:4.1f}%  E{r['design'].positions[0][0]:,.1f}/"
              f"N{r['design'].positions[0][1]:,.1f}")
    top_pos = [r["design"].positions[0] for r in pos_rows[:3]]

    # ── 2단계: 방위 × 종횡비 ────────────────────────────────────────────
    print(f"\n2단계 방위×종횡비: 상위 3개 위치 × 방위 12 × 종횡비 4")
    shape_rows = []
    for pos in top_pos:
        for az in range(0, 180, 15):
            for asp in (1.5, 2.0, 2.5, 3.0):
                if not envelope.contains(outline_polygon(plate0, asp, az, *pos)):
                    continue
                d = build_design(1, F0, asp, float(az), [pos], gfa)
                m, v = screen(d)
                shape_rows.append(record(d, m, v, "2-형상"))
    shape_rows.sort(key=lambda r: r["key"])
    for r in shape_rows[:4]:
        d = r["design"]
        print(f"   {r['m']['pass_pct']:5.1f}%  불충족평균 {r['m']['fail_mean_h']:.2f}h  "
              f"{d.aspect:g}:1·방위{d.azimuth:g}°  조망 {r['view']:4.1f}%")
    top_shape = [(r["design"].aspect, r["design"].azimuth, r["design"].positions[0])
                 for r in shape_rows[:3]]

    # ── 3단계: 층수(=판 크기) ───────────────────────────────────────────
    print(f"\n3단계 층수: 상위 3개 형상 × 층수 35~80")
    floor_rows = []
    for asp, az, pos in top_shape:
        for F in range(35, 81, 5):
            plate = gfa / F
            if plate > ctx["max_footprint"]:
                continue
            if not envelope.contains(outline_polygon(plate, asp, az, *pos)):
                continue
            d = build_design(1, F, asp, az, [pos], gfa)
            m, v = screen(d)
            floor_rows.append(record(d, m, v, "3-층수"))
    floor_rows.sort(key=lambda r: r["key"])
    for r in floor_rows[:5]:
        d = r["design"]
        print(f"   {r['m']['pass_pct']:5.1f}%  불충족평균 {r['m']['fail_mean_h']:.2f}h  "
              f"{d.floors}층({d.plate_m2:.0f}㎡)·{d.aspect:g}:1·방위{d.azimuth:g}°  "
              f"조망 {r['view']:4.1f}%")

    # ── 4단계: 상위 후보 주변 위치 재수렴(3m 격자) ──────────────────────
    print(f"\n4단계 위치 재수렴: 상위 4개 구성 × 주변 3m 격자")
    fine_rows = []
    seen: set[tuple] = set()
    for r in floor_rows[:4]:
        d0 = r["design"]
        plate = gfa / d0.floors
        for cx, cy in grid_centres(envelope, plate, d0.aspect, d0.azimuth, 3.0):
            key = (d0.floors, d0.aspect, d0.azimuth, round(cx, 1), round(cy, 1))
            if key in seen:
                continue
            seen.add(key)
            d = build_design(1, d0.floors, d0.aspect, d0.azimuth, [(cx, cy)], gfa)
            m, v = screen(d)
            fine_rows.append(record(d, m, v, "4-정밀위치"))
    fine_rows.sort(key=lambda r: r["key"])
    print(f"   {len(fine_rows)}개 평가, 최상위 "
          f"{fine_rows[0]['m']['pass_pct']:.1f}%")

    # ── 5단계: 2개동 대안 ───────────────────────────────────────────────
    print(f"\n5단계 2개동 대안 검토")
    two_rows = []
    best1 = fine_rows[0]["design"]
    for F in (40, 50, 60, 70):
        plate = gfa / (2 * F)
        for az in (best1.azimuth, (best1.azimuth + 90) % 180):
            for sep in (35.0, 50.0, 65.0, 80.0):
                ux = math.sin(math.radians(az)) * sep / 2
                uy = math.cos(math.radians(az)) * sep / 2
                for cx, cy in grid_centres(envelope, plate, 2.0, az, 8.0):
                    p1 = (cx + ux, cy + uy)
                    p2 = (cx - ux, cy - uy)
                    t1 = outline_polygon(plate, 2.0, az, *p1)
                    t2 = outline_polygon(plate, 2.0, az, *p2)
                    if not (envelope.contains(t1) and envelope.contains(t2)):
                        continue
                    if t1.intersects(t2):
                        continue
                    d = build_design(2, F, 2.0, az, [p1, p2], gfa)
                    m, v = screen(d)
                    two_rows.append(record(d, m, v, "5-2개동"))
    two_rows.sort(key=lambda r: r["key"])
    if two_rows:
        r = two_rows[0]
        print(f"   2개동 최선 {r['m']['pass_pct']:.1f}% "
              f"({r['design'].floors}층×2, 판 {r['design'].plate_m2:.0f}㎡) vs "
              f"1개동 최선 {fine_rows[0]['m']['pass_pct']:.1f}%")

    # ── 6단계: 상위 후보 전체 수광점 정밀 재평가 ────────────────────────
    cands = (fine_rows[:8] + floor_rows[:4] + two_rows[:4])
    uniq: dict[tuple, dict] = {}
    for r in cands:
        d = r["design"]
        uniq[(d.n_towers, d.floors, d.aspect, d.azimuth,
              tuple(round(v, 1) for p in d.positions for v in p))] = r
    print(f"\n6단계 정밀 재평가: {len(uniq)}개 후보(전체 수광점)")
    final: list[dict[str, Any]] = []
    for r in uniq.values():
        d = r["design"]
        m, v = full(d)
        final.append({"design": d, "m": m, "view": v,
                      "key": rank_key(m, d, v)})
    final.sort(key=lambda r: r["key"])

    print(f"\n{'순위':>3} {'구성':<34}{'교사동A':>8}{'불충족h':>8}{'운동장A':>8}"
          f"{'교실평균h':>9}{'조망':>7}{'건폐율':>7}")
    print("-" * 88)
    for i, r in enumerate(final[:args.top_n], 1):
        d, m = r["design"], r["m"]
        bcr = d.footprint_m2 / args.site_area * 100
        print(f"{i:>3} {d.label():<34}{m['pass_pct']:>7.1f}%{m['fail_mean_h']:>8.2f}"
              f"{m['pg_pass_pct']:>7.1f}%{m['mean_total_h']:>9.2f}"
              f"{r['view']:>6.1f}%{bcr:>6.2f}%")

    best = final[0]
    report(args, ctx, best, final, base_now, base_none, view)
    return 0


def report(args, ctx, best, final, base_now, base_none, view) -> None:
    """확정안 상세·학교별 영향·이격 검토·산출물."""
    import copy
    d = best["design"]
    receptors, times, context = ctx["receptors"], ctx["times"], ctx["context"]
    masks = H.build_context_masks(context, receptors, times)
    horiz = {label: info["kind"] == "운동장" for label, info in ctx["units"].items()}

    def by_unit(prisms) -> dict[str, float]:
        res = H.evaluate(receptors, prisms, times, masks, args.step_min)
        agg: dict[str, list[float]] = defaultdict(list)
        for r in res:
            agg[r["building"]].append(r["total_h_08_16"])
        return {k: sum(v) / len(v) for k, v in agg.items()}

    h_none = by_unit([])
    h_now = by_unit(ctx["existing"])
    h_new = by_unit(d.prisms)

    lng, sht = d.plate_dims
    b = d.balcony_m
    print(f"\n■ 확정 설계안: {d.label()}")
    print(f"   연면적선(내부 골조) {d.plate_m2:,.1f}㎡ ({lng:.2f}×{sht:.2f}m)")
    print(f"   외형선(발코니 끝)   {d.outline_m2:,.1f}㎡ "
          f"({lng+2*b:.2f}×{sht+2*b:.2f}m) ← 그림자·이격 기준")
    print(f"   건축면적선(발코니끝−1m) {d.coverage_m2:,.1f}㎡ · "
          f"서비스면적 {d.service_m2:,.0f}㎡ · 높이 {d.height_m:.1f}m")
    print(f"   연면적 {d.gfa_m2:,.0f}㎡ (용적률 {d.gfa_m2/args.site_area*100:.1f}%) · "
          f"건축면적 {d.footprint_m2:,.1f}㎡ (건폐율 "
          f"{d.footprint_m2/args.site_area*100:.2f}%)")
    for i, (x, y) in enumerate(d.positions, 1):
        print(f"   {i}동 중심 E{x:,.1f} / N{y:,.1f}")
    gap = min(ctx["site"].exterior.distance(p.footprint) for p in d.prisms)
    print(f"   대지경계 최소이격 {gap:.1f}m "
          f"(채광이격 1/2 기준 {d.height_m/2:.1f}m → "
          f"{'충족' if gap >= d.height_m/2 else '미충족(완화·조례 검토 필요)'})")
    print(f"   한강 조망 개방률 {best['view']:.1f}%")

    print(f"\n■ 학교 교사동·운동장별 평균 일조시간(시간)")
    print(f"{'구분':<26}{'종류':<7}{'화랑없음':>9}{'기존10층':>9}{'신축안':>9}"
          f"{'신축−기존':>10}{'신축−없음':>10}")
    print("-" * 82)
    order = sorted(ctx["units"], key=lambda k: (ctx["units"][k]["jibun"],
                                                ctx["units"][k]["kind"] != "교사동", k))
    for label in order:
        info = ctx["units"][label]
        print(f"{label:<26}{info['kind']:<7}{h_none[label]:>9.2f}{h_now[label]:>9.2f}"
              f"{h_new[label]:>9.2f}{h_new[label]-h_now[label]:>+10.2f}"
              f"{h_new[label]-h_none[label]:>+10.2f}")

    # 학교 단위 집계
    print(f"\n■ 학교별 종합")
    per: dict[str, list[str]] = defaultdict(list)
    for label, info in ctx["units"].items():
        per[f"{info['jibun']} {info['school']}"].append(label)
    for school in sorted(per):
        labels = per[school]
        n = sum(ctx["units"][k]["n"] for k in labels)
        w = lambda hh: sum(hh[k] * ctx["units"][k]["n"] for k in labels) / n
        print(f"   {school:<24} 화랑없음 {w(h_none):.2f}h → 기존 {w(h_now):.2f}h "
              f"→ 신축안 {w(h_new):.2f}h  (기존 대비 {w(h_new)-w(h_now):+.2f}h)")

    # 용적률 민감도
    print(f"\n■ 용적률 민감도 (같은 형상·위치에서 연면적만 축소)")
    print(f"{'용적률':<9}{'층수':>6}{'교사동A':>9}{'불충족h':>9}{'교실평균h':>10}")
    print("-" * 45)
    far_rows = []
    for far in (args.far, args.far * 0.875, args.far * 0.75):
        gfa2 = args.site_area * far / 100.0
        F2 = max(1, round(gfa2 / d.plate_m2))
        d2 = build_design(d.n_towers, F2, d.aspect, d.azimuth, d.positions,
                          gfa2 * 1.0)
        # 판 크기를 유지하고 층수만 줄인다
        d2 = Design(d.n_towers, F2, d.aspect, d.azimuth, d.positions, d.plate_m2,
                    [H.Prism(p.footprint, F2 * RESI_FLOOR_H + ROOFTOP_M, p.label)
                     for p in d.prisms])
        m2 = metrics_of(H.evaluate(receptors, d2.prisms, times, masks,
                                   args.step_min), horiz)
        far_rows.append((far, F2, m2))
        print(f"{far:>6.0f}%{F2:>6}{m2['pass_pct']:>8.1f}%{m2['fail_mean_h']:>9.2f}"
              f"{m2['mean_total_h']:>10.2f}")

    write_outputs(args, ctx, best, final, h_none, h_now, h_new, far_rows, gap)


def write_outputs(args, ctx, best, final, h_none, h_now, h_new, far_rows, gap) -> None:
    out = args.outdir
    d = best["design"]

    with (out / "hwarang_redesign_candidates.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["순위", "동수", "층수", "종횡비", "방위", "기준층㎡", "높이m",
                    "연면적㎡", "용적률%", "건폐율%", "교사동기준A%", "불충족평균h",
                    "운동장기준A%", "교실평균h", "한강조망%", "중심좌표"])
        for i, r in enumerate(final, 1):
            dd, m = r["design"], r["m"]
            w.writerow([i, dd.n_towers, dd.floors, dd.aspect, dd.azimuth,
                        round(dd.plate_m2, 1), round(dd.height_m, 1),
                        round(dd.gfa_m2), round(dd.gfa_m2 / args.site_area * 100, 1),
                        round(dd.footprint_m2 / args.site_area * 100, 2),
                        round(m["pass_pct"], 1), round(m["fail_mean_h"], 2),
                        round(m["pg_pass_pct"], 1), round(m["mean_total_h"], 2),
                        round(r["view"], 1),
                        " / ".join(f"E{x:.1f},N{y:.1f}" for x, y in dd.positions)])

    with (out / "hwarang_redesign_units.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["구분", "종류", "학교", "지번", "수광점",
                    "화랑없음h", "기존10층h", "신축안h", "신축−기존", "신축−없음"])
        for label, info in sorted(ctx["units"].items(),
                                  key=lambda kv: (kv[1]["jibun"], kv[0])):
            w.writerow([label, info["kind"], info["school"], info["jibun"],
                        info["n"], round(h_none[label], 2), round(h_now[label], 2),
                        round(h_new[label], 2),
                        round(h_new[label] - h_now[label], 2),
                        round(h_new[label] - h_none[label], 2)])

    feats = [{"type": "Feature", "geometry": mapping(p.footprint),
              "properties": {"label": p.label, "floors": d.floors,
                             "height_m": round(d.height_m, 2),
                             "plate_m2": round(d.plate_m2, 1),
                             "top_elev_m": round(H.GROUND_ELEV_M + d.height_m, 2)}}
             for p in d.prisms]
    feats.append({"type": "Feature", "geometry": mapping(ctx["site"]),
                  "properties": {"label": "화랑 대지", "area_m2": round(ctx["site"].area, 1)}})
    feats.append({"type": "Feature", "geometry": mapping(ctx["envelope"]),
                  "properties": {"label": f"이격선 {args.setback:g}m"}})
    (out / "hwarang_redesign_best.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
        "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")

    export_plan(out / "hwarang_redesign_plan.svg", args, ctx, best, h_now, h_new)
    print(f"\n결과 저장: {out}")


def export_plan(path: Path, args, ctx, best, h_now, h_new) -> None:
    """확정안 배치도 – 학교 교사동별 일조시간 변화 표기."""
    d = best["design"]
    layers = ([ctx["site"], *(p.footprint for p in d.prisms)]
              + [info["geom"] for info in ctx["units"].values()]
              + [p.footprint for p in ctx["daegyo_new"]]
              + [p.footprint for p in ctx["existing"]])
    minx, miny, maxx, maxy = unary_union(layers).buffer(28.0).bounds
    px_w = 1200.0
    scale = px_w / (maxx - minx)
    px_h = (maxy - miny) * scale
    top, bottom = 104.0, 118.0

    def path_of(geom) -> str:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return "".join('<polygon points="' + " ".join(
            f"{(x-minx)*scale:.1f},{(maxy-y)*scale:.1f}"
            for x, y in p.exterior.coords) + '" />' for p in polys)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h+top+bottom:.0f}" viewBox="0 0 {px_w:.0f} '
           f'{px_h+top+bottom:.0f}">',
           '<rect width="100%" height="100%" fill="#fcfcfa"/>',
           '<text x="16" y="30" font-family="sans-serif" font-size="19" '
           f'font-weight="bold" fill="#111">화랑아파트 최적 설계안 – {d.label()}</text>',
           '<text x="16" y="54" font-family="sans-serif" font-size="12.5" fill="#555">'
           f'기준층 {d.plate_m2:,.0f}㎡ · 높이 {d.height_m:.1f}m · 용적률 '
           f'{d.gfa_m2/args.site_area*100:.0f}% · 건폐율 '
           f'{d.footprint_m2/args.site_area*100:.2f}% · 한강조망 {best["view"]:.0f}% '
           f'· 교사동 기준A {best["m"]["pass_pct"]:.1f}%</text>',
           '<text x="16" y="74" font-family="sans-serif" font-size="12.5" fill="#555">'
           '교사동 숫자 = 동지일 평균 일조시간: 화랑 기존 10층 → 본 설계안 '
           '(대교 재건축안이 지어진 상태 기준)</text>',
           f'<g transform="translate(0,{top:.0f})">']

    for label, info in ctx["units"].items():
        if info["kind"] != "운동장":
            continue
        svg.append(f'<g fill="#e4eeda" fill-opacity="0.38" stroke="#9ab37c" '
                   f'stroke-width="1" stroke-dasharray="5 4">{path_of(info["geom"])}</g>')
    for p in ctx["context"]:
        svg.append(f'<g fill="#e8e8e3" fill-opacity="0.65" stroke="#d2d2cb" '
                   f'stroke-width="0.5">{path_of(p.footprint)}</g>')
    for p in ctx["daegyo_new"]:
        col = "#5c6b8a" if p.top_m >= 50 else "#b9c0cc"
        svg.append(f'<g fill="{col}" fill-opacity="0.85" stroke="#3b4a63" '
                   f'stroke-width="1">{path_of(p.footprint)}</g>')
    for label, info in ctx["units"].items():
        if info["kind"] == "운동장":
            continue
        delta = h_new[label] - h_now[label]
        col = ("#c1121f" if delta <= -1.0 else "#e07a5f" if delta <= -0.3
               else "#81b29a" if delta > 0.1 else "#a8a8a0")
        svg.append(f'<g fill="{col}" fill-opacity="0.92" stroke="#2b2b2b" '
                   f'stroke-width="1.2">{path_of(info["geom"])}</g>')
        c = info["geom"].centroid
        cx, cy = (c.x - minx) * scale, (maxy - c.y) * scale
        svg.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="sans-serif" '
                   f'font-size="11.5" font-weight="bold" text-anchor="middle" '
                   f'fill="#fff" stroke="#000" stroke-width="0.5" '
                   f'paint-order="stroke">{label.rsplit(" ",1)[-1]}</text>')
        svg.append(f'<text x="{cx:.1f}" y="{cy+13:.1f}" font-family="sans-serif" '
                   f'font-size="10" text-anchor="middle" fill="#111">'
                   f'{h_now[label]:.1f}→{h_new[label]:.1f}h</text>')
    for label, info in ctx["units"].items():
        if info["kind"] == "운동장":
            continue
    # 학교명
    per: dict[str, list] = defaultdict(list)
    for label, info in ctx["units"].items():
        if info["kind"] == "교사동":
            per[f'{info["jibun"]} {info["school"]}'].append(info["geom"])
    for school, geoms in per.items():
        g = unary_union(geoms)
        cx = (g.centroid.x - minx) * scale
        cy = (maxy - g.bounds[3]) * scale - 9
        svg.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="sans-serif" '
                   f'font-size="14" font-weight="bold" text-anchor="middle" '
                   f'fill="#0b1c30" stroke="#fcfcfa" stroke-width="4" '
                   f'paint-order="stroke">{school}</text>')
    svg.append(f'<g fill="none" stroke="#e63946" stroke-width="2.4" '
               f'stroke-dasharray="10 5">{path_of(ctx["site"])}</g>')
    for p in ctx["existing"]:
        svg.append(f'<g fill="none" stroke="#8d99ae" stroke-width="1.5" '
                   f'stroke-dasharray="6 4">{path_of(p.footprint)}</g>')
    for p in d.prisms:
        svg.append(f'<g fill="#1d3557" fill-opacity="0.95" stroke="#0b1c30" '
                   f'stroke-width="1.6">{path_of(p.footprint)}</g>')
        c = p.footprint.centroid
        svg.append(f'<text x="{(c.x-minx)*scale:.1f}" y="{(maxy-c.y)*scale:.1f}" '
                   f'font-family="sans-serif" font-size="11" font-weight="bold" '
                   f'text-anchor="middle" fill="#fff">{d.floors}F</text>')
    svg.append(f'<g transform="translate({px_w-70:.0f},22)">'
               '<line x1="0" y1="42" x2="0" y2="4" stroke="#111" stroke-width="2"/>'
               '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
               '<text x="0" y="58" font-family="sans-serif" font-size="12" '
               'text-anchor="middle" fill="#111">N</text></g>')
    bx, by = 18.0, px_h - 18.0
    svg.append(f'<line x1="{bx}" y1="{by}" x2="{bx+50*scale:.1f}" y2="{by}" '
               f'stroke="#111" stroke-width="3"/>'
               f'<text x="{bx+25*scale:.1f}" y="{by-7:.0f}" font-family="sans-serif" '
               f'font-size="11.5" text-anchor="middle" fill="#111">50 m</text>')
    svg.append("</g>")
    legend = [("#1d3557", "화랑 신축(본 설계안)"), ("#ffffff", "화랑 기존 10층(점선)"),
              ("#5c6b8a", "대교 신축 타워"), ("#b9c0cc", "대교 신축 저층부"),
              ("#c1121f", "교사동 1h↑ 감소"), ("#e07a5f", "0.3~1h 감소"),
              ("#a8a8a0", "거의 변화 없음"), ("#81b29a", "증가")]
    for i, (col, txt) in enumerate(legend):
        x = 18 + (i % 4) * 296
        y = px_h + top + 30 + (i // 4) * 34
        svg.append(f'<rect x="{x}" y="{y-11}" width="14" height="14" fill="{col}" '
                   f'fill-opacity="0.9" stroke="#333" stroke-width="0.7"/>')
        svg.append(f'<text x="{x+21}" y="{y+1}" font-family="sans-serif" '
                   f'font-size="12" fill="#111">{txt}</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
