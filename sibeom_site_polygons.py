#!/usr/bin/env python3
"""여의도 시범아파트 재건축 배치도 → 일조권 분석용 폴리곤(EPSG:5186).

대교아파트(`daegyo_site_polygons.py`)와 같은 역할을 한다. 배치도 래스터에서
읽은 픽셀좌표를 실좌표로 바꾸고, 동별 건축물높이를 붙여 QGIS·그림자 분석에
바로 쓸 수 있는 레이어를 낸다.

입력
  data/sibeom_site_plan.json   좌표등록 정의 + 구역경계 + 동별 footprint(픽셀)
  data/sibeom_dong_spec.json   일조평가 모델링 상세 제원(동별 층수·건축물높이)

좌표등록은 배치도에 치수 기준선이 없어 세 가지를 조합해 잡았다.
  축척   정비구역면적 109,307.80㎡ 에 구역경계 내부면적을 맞춰 역산
  회전   여의도 블록 장축 51.7°(AL_D010 기존 시범 건물군 최소외접사각형)
  위치   기존 시범 26개동은 구역 안, 주변 건물은 구역 밖이라는 조건

    python3 sibeom_site_polygons.py
    python3 sibeom_site_polygons.py --buildings <AL_D010.gpkg>   # 검수도 포함
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

DEFAULT_PLAN = Path(__file__).with_name("data") / "sibeom_site_plan.json"
DEFAULT_SPEC = Path(__file__).with_name("data") / "sibeom_dong_spec.json"
DEFAULT_OUTDIR = Path(__file__).with_name("outputs") / "sibeom"


class Transform:
    """배치도 픽셀 → EPSG:5186 (축척·회전·평행이동)."""

    def __init__(self, geo: dict[str, Any]) -> None:
        self.s = float(geo["scale_m_per_px"])
        self.az = math.radians(float(geo["plan_up_azimuth_deg"]))
        self.opx, self.opy = geo["origin_px"]
        self.ox, self.oy = geo["origin_epsg5186"]

    def __call__(self, px: float, py: float) -> tuple[float, float]:
        u = (px - self.opx) * self.s
        v = -(py - self.opy) * self.s
        c, s = math.cos(self.az), math.sin(self.az)
        return (self.ox + u * c + v * s, self.oy - u * s + v * c)

    def ring(self, pts: Sequence[Sequence[float]]) -> list[tuple[float, float]]:
        return [self(x, y) for x, y in pts]


def load_heights(spec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {d["dong"]: d["zones"] for d in spec["dongs"]}


def write_geojson(path: Path, feats: list[dict[str, Any]], to_wgs=None) -> None:
    out = []
    for f in feats:
        g = f["geometry"]
        if to_wgs is not None:
            g = {"type": g["type"],
                 "coordinates": [[list(to_wgs.transform(x, y)) for x, y in ring]
                                 for ring in g["coordinates"]]}
        out.append({"type": "Feature", "geometry": g, "properties": f["properties"]})
    crs = ("EPSG:4326" if to_wgs is not None else "EPSG:5186")
    path.write_text(json.dumps(
        {"type": "FeatureCollection",
         "crs": {"type": "name", "properties": {"name": crs}},
         "features": out}, ensure_ascii=False, indent=1), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    p.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument("--buildings", type=Path, default=None,
                   help="AL_D010 GeoPackage. 주면 좌표등록 검수 수치를 함께 낸다")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    args.outdir.mkdir(parents=True, exist_ok=True)
    T = Transform(plan["georeference"])
    ap = plan["approval"]
    gl = float(plan["levels"]["planned_ground_elev_m"])
    heights = load_heights(spec)

    feats: list[dict[str, Any]] = []
    district = Polygon(T.ring(plan["district_boundary_px"]))
    feats.append({"geometry": mapping(district), "properties": {
        "layer": "정비구역경계", "name": "정비구역", "area_m2": round(district.area, 1),
        "approved_m2": ap["district_area_m2"]}})

    b_feats: list[dict[str, Any]] = []
    for b in plan.get("buildings", []):
        poly = Polygon(T.ring(b["ring_px"]))
        zone = b.get("zone_index", 0)
        zs = heights.get(b["dong"], [])
        z = zs[zone] if zone < len(zs) else {}
        b_feats.append({"geometry": mapping(poly), "properties": {
            "layer": "신축동", "dong": b["dong"], "zone": b.get("part", ""),
            "floors": z.get("floors"), "height_m": z.get("height_m"),
            "ground_m": gl, "top_elev_m": None if z.get("height_m") is None
            else round(gl + z["height_m"], 2),
            "area_m2": round(poly.area, 1)}})
    feats += b_feats

    try:
        from pyproj import CRS, Transformer
        to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                      always_xy=True)
    except ImportError:
        to_wgs = None

    write_geojson(args.outdir / "sibeom_epsg5186.geojson", feats)
    if to_wgs is not None:
        write_geojson(args.outdir / "sibeom_wgs84.geojson", feats, to_wgs)

    with (args.outdir / "sibeom_buildings.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["레이어", "동", "구간", "층수", "건축물높이_m", "지반고_m",
                    "최고표고_m", "건축면적_m2", "WKT_EPSG5186"])
        for f, g in ((f, Polygon(f["geometry"]["coordinates"][0])) for f in feats):
            q = f["properties"]
            w.writerow([q["layer"], q.get("dong", ""), q.get("zone", ""),
                        q.get("floors", ""), q.get("height_m", ""),
                        q.get("ground_m", ""), q.get("top_elev_m", ""),
                        q.get("area_m2", ""), g.wkt])

    built = unary_union([Polygon(f["geometry"]["coordinates"][0])
                         for f in b_feats]) if b_feats else None
    lines = [
        "# 여의도 시범아파트 재건축 – 일조권 분석용 폴리곤",
        "",
        "## 인가 제원 (건축개요 2.1.2)",
        "",
        "| 항목 | 값 |", "|---|---:|",
        f"| 정비구역면적 | {ap['district_area_m2']:,.2f} ㎡ |",
        f"| 대지면적 | {ap['site_area_m2']:,.2f} ㎡ |",
        f"| 건축면적 | {ap['building_area_m2']:,.2f} ㎡ |",
        f"| 연면적(지상) | {ap['gfa_above_m2']:,.2f} ㎡ |",
        f"| 건폐율 | {ap['building_coverage_ratio_pct']:.2f}% |",
        f"| 용적률 | {ap['floor_area_ratio_pct']:.2f}% |",
        f"| 규모 | {ap['scale_label']} · {ap['household_count']:,}세대 |",
        "",
        "## 좌표등록 검증",
        "",
        "| 항목 | 값 |", "|---|---|",
        f"| 축척 | {T.s:.4f} m/화소 |",
        f"| 도면 위쪽 방위 | {math.degrees(T.az):.1f}° |",
        f"| 구역경계 면적 | {district.area:,.1f} ㎡ "
        f"(인가 {ap['district_area_m2']:,.2f} ㎡, 오차 "
        f"{abs(district.area - ap['district_area_m2']) / ap['district_area_m2'] * 100:.2f}%) |",
        f"| 절대 위치 불확실성 | ±{plan['georeference']['calibration']['residual']['plan_uncertainty_m']:.0f} m |",
        "",
        "## 동별 건축물높이 (일조평가 모델링 상세 제원)",
        "",
        "| 동 | 층수 | 건축물높이(m) | 최고표고(GL+{:.1f}m) |".format(gl),
        "|---|---:|---:|---:|",
    ]
    for d in spec["dongs"]:
        for z in d["zones"]:
            lines.append(f"| {d['dong']} | {z['floors']} | {z['height_m']:.2f} | "
                         f"{gl + z['height_m']:.2f} |")
    lines += ["",
              f"동 {len(spec['dongs'])}개 · 구간 "
              f"{sum(len(d['zones']) for d in spec['dongs'])}개 · 최고 "
              f"{max(z['height_m'] for d in spec['dongs'] for z in d['zones']):.2f} m",
              ""]
    if built is None:
        lines += ["## 남은 것", "",
                  "**동별 footprint 폴리곤이 아직 없다.** 첨부 배치도 래스터로는",
                  "자동 추출이 되지 않는다(색상 블록 합이 건축면적의 22%).",
                  "`data/sibeom_site_plan.json` 의 `_buildings_todo` 참조.", ""]
    else:
        lines += ["## 건축면적 검증", "",
                  f"트레이싱 합계 {built.area:,.1f} ㎡ / 인가 "
                  f"{ap['building_area_m2']:,.2f} ㎡ "
                  f"(오차 {abs(built.area - ap['building_area_m2']) / ap['building_area_m2'] * 100:.2f}%)",
                  ""]
    (args.outdir / "sibeom_report.md").write_text("\n".join(lines), encoding="utf-8")

    print("■ 여의도 시범아파트 – 일조권 분석용 폴리곤")
    print(f"   축척 {T.s:.4f} m/화소 · 도면 위쪽 방위 {math.degrees(T.az):.1f}°")
    print(f"   정비구역 {district.area:,.1f} ㎡ (인가 {ap['district_area_m2']:,.2f} ㎡, "
          f"오차 {abs(district.area - ap['district_area_m2']) / ap['district_area_m2'] * 100:.2f}%)")
    print(f"   동 {len(spec['dongs'])}개 · 구간 "
          f"{sum(len(d['zones']) for d in spec['dongs'])}개 · 최고 "
          f"{max(z['height_m'] for d in spec['dongs'] for z in d['zones']):.2f} m "
          f"(GL+{gl} → 표고 "
          f"{gl + max(z['height_m'] for d in spec['dongs'] for z in d['zones']):.2f} m)")
    print(f"   동별 footprint {len(b_feats)}개"
          + ("  ← 아직 없음. 배치도 원본(모델링 배치도/DWG)이 필요하다"
             if not b_feats else ""))
    print(f"   → {args.outdir}")

    if args.buildings and args.buildings.exists():
        from daegyo_school_sunlight import load_named_buildings
        bl = load_named_buildings(args.buildings)
        sib = unary_union([r["geom"] for r in bl if r.get("jibun") == "50"])
        nb = unary_union([r["geom"] for r in bl if r.get("jibun") != "50"
                          and r["geom"].intersects(district.buffer(50))])
        print(f"   검수: 기존 시범 건물 포함률 "
              f"{sib.intersection(district).area / sib.area * 100:.1f}% · "
              f"주변 건물 침범 {nb.intersection(district).area:,.0f} ㎡")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
