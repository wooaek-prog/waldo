#!/usr/bin/env python3
"""화랑 재건축 – '신규 불충족 0' 설계안 탐색.

목표가 바뀌었다. 종전에는 학교 일조 **충족률을 최대화**했는데, 이제는

    현재(대교 신축 + 화랑 기존)에서 충족하던 수광점이
    **단 하나도 불충족으로 떨어지지 않는** 안

을 찾는다. 원래부터 불충족이던 점은 그대로 두어도 된다.

설계 조건
    용적률 400% · 건폐율 60% · 층수 39~55층
    그림자·건축면적은 **발코니(서비스면적) 1.5m 를 포함한 외형선** 기준
    일조가 어려우면 **저층부(1~10층)만 면적을 키우는** 안(포디움)도 허용

핵심 물리: 용적률이 고정이므로 연면적 = 기준층 × 층수가 상수다. 저층부로
연면적을 옮기면 타워 기준층이 작아져 **그림자 폭이 좁아지고**, 저층부 자체는
높이가 낮아(10층 ≈ 29.5m) 동지 정오 그림자가 53m 밖에 못 간다. 학교까지
60m 이상이면 저층부 그림자는 학교에 닿지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon
from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import (
    HWARANG_JIBUN, apartment_prisms, load_named_buildings,
)
from hwarang_redesign import BALCONY_M, COVERAGE_INSET_M, plate_polygon
from school_receptor_compliance import (
    build_context, build_points, pass_a,
)

RESI_FLOOR_H = H.RESI_FLOOR_H       # 2.95
ROOFTOP_M = H.ROOFTOP_M             # 4.0
PODIUM_FLOORS = 10                  # '저층부' 정의 = 1~10층


@dataclass
class Design:
    floors: int
    aspect: float                   # 타워 장변:단변(연면적선)
    azimuth: float                  # 타워 장변 방위
    cx: float
    cy: float
    tower_plate: float              # 타워 기준층 연면적선(㎡)
    podium_floors: int = 0
    podium_plate: float = 0.0       # 저층부 기준층 연면적선(㎡)
    podium_aspect: float = 0.0
    podium_cx: float = 0.0
    podium_cy: float = 0.0
    podium_azimuth: float = 0.0     # 저층부는 대지 장축에 맞춘다

    @property
    def height_m(self) -> float:
        return self.floors * RESI_FLOOR_H + ROOFTOP_M

    @property
    def podium_top_m(self) -> float:
        return self.podium_floors * RESI_FLOOR_H

    @property
    def gfa_m2(self) -> float:
        return (self.tower_plate * (self.floors - self.podium_floors)
                + self.podium_plate * self.podium_floors)

    def dims(self, area: float, aspect: float) -> tuple[float, float]:
        short = math.sqrt(area / aspect)
        return area / short, short

    def coverage_m2(self) -> float:
        """건축면적 = 지상 투영면적(외형선 −1m). 저층부가 있으면 그것이 최대."""
        d = BALCONY_M - COVERAGE_INSET_M
        lng, sht = self.dims(self.tower_plate, self.aspect)
        tower = (lng + 2 * d) * (sht + 2 * d)
        if self.podium_floors <= 0:
            return tower
        plng, psht = self.dims(self.podium_plate, self.podium_aspect)
        return max(tower, (plng + 2 * d) * (psht + 2 * d))

    def service_m2(self) -> float:
        b = BALCONY_M
        lng, sht = self.dims(self.tower_plate, self.aspect)
        out = ((lng + 2 * b) * (sht + 2 * b) - self.tower_plate) \
            * (self.floors - self.podium_floors)
        if self.podium_floors > 0:
            plng, psht = self.dims(self.podium_plate, self.podium_aspect)
            out += ((plng + 2 * b) * (psht + 2 * b) - self.podium_plate) \
                * self.podium_floors
        return out

    def prisms(self) -> list[H.Prism]:
        out = [H.Prism(plate_polygon(self.tower_plate, self.aspect, self.azimuth,
                                     self.cx, self.cy, BALCONY_M),
                       self.height_m, f"화랑 타워 {self.floors}F")]
        if self.podium_floors > 0:
            out.append(H.Prism(
                plate_polygon(self.podium_plate, self.podium_aspect,
                              self.podium_azimuth, self.podium_cx,
                              self.podium_cy, BALCONY_M),
                self.podium_top_m, f"화랑 저층부 {self.podium_floors}F"))
        return out

    def outlines(self) -> list[tuple[str, Polygon]]:
        out = [("타워 외형선", plate_polygon(self.tower_plate, self.aspect,
                                          self.azimuth, self.cx, self.cy,
                                          BALCONY_M))]
        if self.podium_floors > 0:
            out.append(("저층부 외형선",
                        plate_polygon(self.podium_plate, self.podium_aspect,
                                      self.podium_azimuth, self.podium_cx,
                                      self.podium_cy, BALCONY_M)))
        return out

    def label(self) -> str:
        lng, sht = self.dims(self.tower_plate, self.aspect)
        s = (f"{self.floors}층·{self.aspect:g}:1·방위{self.azimuth:g}°·"
             f"기준층 {self.tower_plate:.0f}㎡({lng:.1f}×{sht:.1f}m)")
        if self.podium_floors > 0:
            plng, psht = self.dims(self.podium_plate, self.podium_aspect)
            s += (f" + 저층부 {self.podium_floors}층 {self.podium_plate:.0f}㎡"
                  f"({plng:.1f}×{psht:.1f}m)")
        return s


# --------------------------------------------------------------------------- #
def evaluate_design(design: Design, receptors, times, masks, step_min: int):
    return H.evaluate(receptors, design.prisms(), times, masks, step_min)


def count_new_fail(results, base_pass: Sequence[bool]) -> tuple[int, int, float]:
    """(신규 불충족 수, 신규 충족 수, 총일조 손실 합)."""
    nf = ng = 0
    loss = 0.0
    for r, ok in zip(results, base_pass):
        now = pass_a(r)
        if ok and not now:
            nf += 1
        elif not ok and now:
            ng += 1
    return nf, ng, loss


def river_view(design: Design, context: Sequence[H.Prism], reach: float,
               az_from: float, az_to: float, step: float = 10.0) -> float:
    """한강 조망 개방률 – 타워 중심에서 강 방향 시선이 열린 비율."""
    az = az_from
    azs = []
    while True:
        azs.append(az % 360)
        if abs((az - az_to) % 360) < 1e-6:
            break
        az += step
        if len(azs) > 200:
            break
    open_n = 0
    cx, cy = design.cx, design.cy
    for a in azs:
        dx, dy = math.sin(math.radians(a)), math.cos(math.radians(a))
        line = Polygon([(cx, cy), (cx + dx * reach, cy + dy * reach),
                        (cx + dx * reach + 0.2, cy + dy * reach + 0.2)])
        if not any(p.footprint.intersects(line) and p.top_m > 20.0
                   for p in context):
            open_n += 1
    return open_n / len(azs) * 100.0 if azs else 0.0


def grid_centres(envelope: Polygon, area: float, aspect: float, azimuth: float,
                 step: float) -> list[tuple[float, float]]:
    minx, miny, maxx, maxy = envelope.bounds
    out: list[tuple[float, float]] = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if envelope.contains(plate_polygon(area, aspect, azimuth, x, y,
                                               BALCONY_M)):
                out.append((round(x, 1), round(y, 1)))
            x += step
        y += step
    return out


def pod_positions(envelope: Polygon, area: float, azimuth: float,
                  step: float = 12.0) -> list[tuple[float, float, float]]:
    """저층부(대지 장축 정렬) 후보 – (세장비, cx, cy). 봉투 안에 드는 것만."""
    out: list[tuple[float, float, float]] = []
    for aspect in (1.6, 2.2, 3.0, 4.0):
        for cx, cy in grid_centres(envelope, area, aspect, azimuth, step):
            out.append((aspect, cx, cy))
    return out


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="화랑 '신규 불충족 0' 설계안 탐색")
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
    p.add_argument("--pos-step", type=float, default=8.0)
    p.add_argument("--budget-a", type=int, default=260)
    p.add_argument("--budget-b", type=int, default=520)
    p.add_argument("--budget-c", type=int, default=780)
    p.add_argument("--budget-d", type=int, default=460)
    p.add_argument("--probe", action="store_true",
                   help="1회 평가 소요시간만 재고 종료")
    return p.parse_args(argv)


def setup(args) -> dict[str, Any]:
    from pyproj import CRS, Transformer
    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    daegyo_new = H.load_daegyo(args.daegyo)
    site = H.build_site(buildings, args.site_area)
    d_centre = unary_union([p.footprint for p in daegyo_new]).centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(d_centre.x, d_centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)

    from daegyo_school_sunlight import school_parcel_rows
    spec_all = SF.load_spec()
    schools = school_parcel_rows(buildings, d_centre, args.school_radius)
    all_b = unary_union([r["geom"] for r in buildings])
    user_pg: dict[str, Polygon] = {}
    if args.playground and args.playground.exists():
        from shapely.geometry import shape
        data = json.loads(args.playground.read_text(encoding="utf-8"))
        for f in data["features"]:
            user_pg[str(f["properties"].get("jibun"))] = shape(f["geometry"])
    points, receptors, grounds = build_points(
        schools, buildings, spec_all, all_b, args.apron, user_pg, args.pg_step)

    context, n_rec = build_context(buildings, d_centre, args.context_radius,
                                   spec_all)
    context = list(context) + list(daegyo_new)   # 대교 신축안은 지어진 전제
    masks = H.build_context_masks(context, receptors, times)
    hwarang_now = apartment_prisms(buildings, HWARANG_JIBUN, "화랑 기존")

    base = H.evaluate(receptors, hwarang_now, times, masks, args.step_min)
    base_pass = [pass_a(r) for r in base]
    return {
        "buildings": buildings, "site": site, "times": times,
        "points": points, "receptors": receptors, "grounds": grounds,
        "context": context, "masks": masks, "daegyo_new": daegyo_new,
        "hwarang_now": hwarang_now, "base": base, "base_pass": base_pass,
        "envelope": site.buffer(-args.setback),
        "gfa": args.site_area * args.far / 100.0,
        "max_cover": args.site_area * args.bcr / 100.0,
        "n_recovered": n_rec,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ctx = setup(args)
    pts, receptors = ctx["points"], ctx["receptors"]
    base_pass = ctx["base_pass"]
    print(f"■ 준비 {time.time()-t0:.0f}s")
    print(f"   수광점 {len(receptors)}개 · 기준(S1: 대교신축+화랑기존) "
          f"충족 {sum(base_pass)}개 / 불충족 {len(base_pass)-sum(base_pass)}개")
    print(f"   대지 {ctx['site'].area:,.0f}㎡ · 연면적 목표 {ctx['gfa']:,.0f}㎡ "
          f"· 건축면적 한도 {ctx['max_cover']:,.0f}㎡")
    print(f"   시점 {len(ctx['times'])}개 · 차폐물 {len(ctx['context'])}개")

    if args.probe:
        d = Design(floors=55, aspect=2.0, azimuth=150.0,
                   cx=ctx["site"].centroid.x, cy=ctx["site"].centroid.y,
                   tower_plate=ctx["gfa"] / 55)
        t = time.time()
        res = evaluate_design(d, receptors, ctx["times"], ctx["masks"],
                              args.step_min)
        nf, ng, _ = count_new_fail(res, base_pass)
        print(f"   1회 평가 {time.time()-t:.1f}s · 신규 불충족 {nf} / 신규 충족 {ng}")
        return 0

    # 참고: 화랑을 헐고 아무것도 짓지 않은 경우 = 신규 불충족의 하한(0)
    none_res = H.evaluate(receptors, [], ctx["times"], ctx["masks"],
                          args.step_min)
    nf0, ng0, _ = count_new_fail(none_res, base_pass)
    print(f"   참고(화랑 철거·미신축): 신규 불충족 {nf0} / 신규 충족 {ng0}\n")

    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def try_design(d: Design, stage: str) -> dict[str, Any] | None:
        key = (d.floors, round(d.aspect, 3), round(d.azimuth, 1),
               round(d.cx, 1), round(d.cy, 1), d.podium_floors,
               round(d.podium_plate, 1), round(d.podium_aspect, 2),
               round(d.podium_cx, 1), round(d.podium_cy, 1))
        if key in seen:
            return None
        seen.add(key)
        if d.coverage_m2() > ctx["max_cover"]:
            return None
        for _name, poly in d.outlines():
            if not ctx["envelope"].contains(poly):
                return None
        res = evaluate_design(d, receptors, ctx["times"], ctx["masks"],
                              args.step_min)
        nf, ng, _ = count_new_fail(res, base_pass)
        lng, sht = d.dims(d.tower_plate, d.aspect)
        row = {
            "stage": stage, "floors": d.floors, "aspect": d.aspect,
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
            "gfa_m2": round(d.gfa_m2, 0),
            "far_pct": round(d.gfa_m2 / args.site_area * 100, 1),
            "height_m": round(d.height_m, 2),
            "mean_total_h": round(sum(r["total_h_08_16"] for r in res)
                                  / len(res), 3),
            "_design": d, "_res": res,
        }
        rows.append(row)
        return row

    site_az, _ = H.site_axes(ctx["site"])
    pod_centre = ctx["envelope"].centroid
    floors_all = list(range(args.floors_min, args.floors_max + 1))
    azimuths = [a * 15.0 for a in range(12)]

    def sweep(tag: str, designs, budget: int) -> list[dict[str, Any]]:
        t = time.time()
        designs = list(designs)
        total = len(designs)
        if total > budget:                      # 균등 간격으로 솎아낸다
            stride = total / budget
            designs = [designs[int(i * stride)] for i in range(budget)]
        print(f"■ {tag}: 후보 {total}개 → {len(designs)}개 평가 중…",
              flush=True)
        got = [r for d in designs if (r := try_design(d, tag))]
        print(f"■ {tag}: {len(got)}개 평가 · {time.time()-t:.0f}s", flush=True)
        if got:
            b = min(got, key=lambda r: (r["new_fail"], -r["floors"]))
            print(f"   최소 신규 불충족 {b['new_fail']}개 — {b['_design'].label()}",
                  flush=True)
        return got

    # ── A. 방위·위치 스캔 (55층·2:1 고정) ────────────────────────────────
    plate55 = ctx["gfa"] / 55
    a_rows = sweep("A 방위·위치", [
        Design(55, 2.0, az, cx, cy, plate55)
        for az in azimuths
        for cx, cy in grid_centres(ctx["envelope"], plate55, 2.0, az, 12.0)],
        args.budget_a)
    if not a_rows:
        raise SystemExit("A단계에서 배치 가능한 후보가 없습니다.")
    bestA = min(a_rows, key=lambda r: (r["new_fail"], -r["floors"]))
    az0, x0, y0 = bestA["azimuth"], bestA["x"], bestA["y"]
    good_az = sorted({r["azimuth"] for r in
                      sorted(a_rows, key=lambda r: r["new_fail"])[:24]})
    print(f"   유망 방위 {good_az}", flush=True)

    # ── B. 층수·세장비 스캔 (A의 최적 방위·위치 부근) ────────────────────
    aspects = (1.25, 1.5, 2.0, 2.5, 3.0, 4.0)
    b_designs = []
    for floors in floors_all[::2]:
        plate = ctx["gfa"] / floors
        for aspect in aspects:
            for az in good_az:
                for cx, cy in grid_centres(ctx["envelope"], plate, aspect, az,
                                           12.0):
                    if math.hypot(cx - x0, cy - y0) <= 26.0:
                        b_designs.append(Design(floors, aspect, az, cx, cy,
                                                plate))
    b_rows = sweep("B 층수·세장비", b_designs, args.budget_b)
    pool = a_rows + b_rows
    bestB = min(pool, key=lambda r: (r["new_fail"], -r["floors"]))

    # ── C. 저층부(1~10층) 확대 ───────────────────────────────────────────
    top_shapes = []
    for r in sorted(pool, key=lambda q: (q["new_fail"], -q["floors"]))[:10]:
        key = (r["aspect"], r["azimuth"], r["x"], r["y"])
        if key not in top_shapes:
            top_shapes.append(key)
    c_designs = []
    for floors in [f for f in (39, 45, 51, 55) if f in floors_all]:
        for pod_plate in (800.0, 1200.0, 1600.0, 2000.0, 2400.0, 2800.0):
            tower_plate = (ctx["gfa"] - pod_plate * PODIUM_FLOORS) \
                / (floors - PODIUM_FLOORS)
            if tower_plate < 330.0:
                continue
            spots = pod_positions(ctx["envelope"], pod_plate, site_az)
            if not spots:
                continue
            for aspect, az, _x, _y in top_shapes[:3]:
                for cx, cy in grid_centres(ctx["envelope"], tower_plate, aspect,
                                           az, 18.0):
                    for pod_aspect, px, py in spots:
                        c_designs.append(Design(floors, aspect, az, cx, cy,
                                                tower_plate, PODIUM_FLOORS,
                                                pod_plate, pod_aspect, px, py,
                                                site_az))
    c_rows = sweep("C 저층부 확대", c_designs, args.budget_c)
    pool += c_rows

    # ── D. 최적 부근 미세조정 ────────────────────────────────────────────
    best = min(pool, key=lambda r: (r["new_fail"], -r["floors"]))
    bd = best["_design"]
    d_designs = []
    for floors in floors_all:
        if bd.podium_floors:
            tp = (ctx["gfa"] - bd.podium_plate * PODIUM_FLOORS) \
                / (floors - PODIUM_FLOORS)
            if tp < 330.0:
                continue
        else:
            tp = ctx["gfa"] / floors
        for aspect in (bd.aspect * f for f in (0.8, 0.9, 1.0, 1.1, 1.25)):
            for daz in (-10.0, -5.0, 0.0, 5.0, 10.0):
                az = (bd.azimuth + daz) % 180
                for dx in (-8.0, -4.0, 0.0, 4.0, 8.0):
                    for dy in (-8.0, -4.0, 0.0, 4.0, 8.0):
                        cx, cy = bd.cx + dx, bd.cy + dy
                        d_designs.append(Design(
                            floors, aspect, az, cx, cy, tp, bd.podium_floors,
                            bd.podium_plate, bd.podium_aspect, bd.podium_cx,
                            bd.podium_cy, bd.podium_azimuth))
    d_rows = sweep("D 미세조정", d_designs, args.budget_d)
    pool += d_rows

    best = min(pool, key=lambda r: (r["new_fail"], -r["floors"],
                                    -r["mean_total_h"]))
    print("\n" + "=" * 78)
    print("■ 최적안")
    print("=" * 78)
    print(f"   {best['_design'].label()}")
    print(f"   신규 불충족 {best['new_fail']}개 · 신규 충족 {best['new_ok']}개")
    print(f"   연면적 {best['gfa_m2']:,.0f}㎡(용적률 {best['far_pct']:.1f}%) · "
          f"건축면적 {best['coverage_m2']:,.0f}㎡(건폐율 {best['bcr_pct']:.2f}%)")
    print(f"   높이 {best['height_m']:.1f}m · 중심 E{best['x']:,.1f}/N{best['y']:,.1f}")
    export_design(args.outdir, best["_design"], ctx, best["_res"], args)
    (args.outdir / "best.json").write_text(json.dumps(
        {k: v for k, v in best.items() if not k.startswith("_")},
        ensure_ascii=False, indent=1), encoding="utf-8")

    write_candidates(args.outdir / "candidates.csv", rows)
    print(f"\n후보 {len(rows)}개 → {args.outdir/'candidates.csv'}")
    return 0


def export_design(outdir: Path, design: Design, ctx: dict[str, Any],
                  res: Sequence[dict[str, Any]], args) -> None:
    """확정안 매싱 GeoJSON + 수광점 결과 + QGIS 레이어."""
    from pyproj import CRS, Transformer
    from school_receptor_compliance import (
        CHANGE_CODE, qml_style, write_geojson, polygon_features,
    )
    to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                  always_xy=True)
    outdir.mkdir(parents=True, exist_ok=True)

    lng, sht = design.dims(design.tower_plate, design.aspect)
    items = [(name, poly, {"kind": name,
                           "height_m": round(design.height_m if "타워" in name
                                             else design.podium_top_m, 2)})
             for name, poly in design.outlines()]
    items.append(("화랑 대지", ctx["site"], {"kind": "대지",
                                          "area_m2": round(ctx["site"].area)}))
    massing = polygon_features(items)
    write_geojson(outdir / "massing_epsg5186.geojson", massing)
    write_geojson(outdir / "massing_wgs84.geojson", massing, to_wgs)

    feats = []
    for p, r, ok in zip(ctx["points"], res, ctx["base_pass"]):
        now = pass_a(r)
        chg = ("유지 충족" if ok and now else "신규 불충족" if ok and not now
               else "신규 충족" if now else "유지 불충족")
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(p.x, 3), round(p.y, 3)]},
            "properties": {
                "pid": p.pid, "school": p.school, "kind": p.kind,
                "dong": p.dong, "floor": p.floor, "source": p.source,
                "z_m": round(p.z, 2),
                "s2_total_h": r["total_h_08_16"],
                "s2_cont_h": r["cont_h_08_16"],
                "s2_pass_a": bool(now), "s1_pass_a": bool(ok),
                "change_cd": CHANGE_CODE[chg], "변화": chg,
            }})
    for stem, sel in (("수광점_전체", None), ("수광점_신규불충족", "new_fail")):
        sub = [f for f in feats
               if sel is None or f["properties"]["change_cd"] == sel]
        a = outdir / f"{stem}_epsg5186.geojson"
        write_geojson(a, sub)
        write_geojson(outdir / f"{stem}_wgs84.geojson", sub, to_wgs)
        a.with_suffix(".qml").write_text(qml_style("change_cd"),
                                         encoding="utf-8")
        (outdir / f"{stem}_wgs84.qml").write_text(qml_style("change_cd"),
                                                  encoding="utf-8")


def write_candidates(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    cols = [c for c in rows[0] if not c.startswith("_")] if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(cols)
        for r in sorted(rows, key=lambda q: (q["new_fail"], -q["floors"])):
            w.writerow([r[c] for c in cols])


if __name__ == "__main__":
    raise SystemExit(main())
