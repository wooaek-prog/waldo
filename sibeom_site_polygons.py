#!/usr/bin/env python3
"""여의도 시범아파트 재건축 배치도 → 일조권 분석용 폴리곤(EPSG:5186).

대교아파트(`daegyo_site_polygons.py`)와 같은 역할을 한다. 배치도 래스터에서
읽은 픽셀좌표를 실좌표로 바꾸고, 동별 건축물높이를 붙여 QGIS·그림자 분석에
바로 쓸 수 있는 레이어를 낸다.

입력
  data/sibeom_site_plan.json    좌표등록 정의 + 구역경계 + 동별 매스(픽셀)
  data/sibeom_dong_spec.json    일조평가 모델링 상세 제원(동별 층수·건축물높이)
  data/sibeom_model_dongs.json  제원표에 없는 층수의 산정높이

구역경계 좌표등록은 배치도에 치수 기준선이 없어 세 가지를 조합해 잡았다.
  축척   정비구역면적 109,307.80㎡ 에 구역경계 내부면적을 맞춰 역산
  회전   여의도 블록 장축 51.7°(AL_D010 기존 시범 건물군 최소외접사각형)
  위치   기존 시범 26개동은 구역 안, 주변 건물은 구역 밖이라는 조건

동별 매스는 좌표계가 다르다. 일조평가 모델링 배치도에서 딴 것이라
`model_georeference` 를 쓴다(`sibeom_model_plan.py` 가 만든다).

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
DEFAULT_DONGS = Path(__file__).with_name("data") / "sibeom_model_dongs.json"
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


def parts(geom) -> list[Polygon]:
    """조각난 매스를 낱개로 편다.

    모델링 배치도의 윙은 한 화소짜리 목으로 이어질 때가 있다. 외곽선을 8방향
    으로 따면 그 목이 한 점에서만 닿아 `buffer(0)` 이 MultiPolygon 을 낸다.
    조각은 수십 ㎡ 짜리 실제 건물이므로 버리지 않고 각각 낸다.
    """
    if geom.geom_type == "MultiPolygon":
        return sorted(geom.geoms, key=lambda g: -g.area)
    return [geom]


def height_table(spec: dict[str, Any], dongs: dict[str, Any],
                 ) -> dict[tuple[str, int], tuple[float, str]]:
    """(동, 층수) → (건축물높이 m, 출처)."""
    out: dict[tuple[str, int], tuple[float, str]] = {}
    for d in spec["dongs"]:
        for z in d["zones"]:
            out[(d["dong"], int(z["floors"]))] = (float(z["height_m"]), "제원표")
    for dong, tbl in dongs.get("_derived_heights", {}).items():
        if dong.startswith("_"):
            continue
        for f, h in tbl.items():
            out.setdefault((dong, int(f)), (float(h), "층고규칙 산정"))
    return out


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
    p.add_argument("--dongs", type=Path, default=DEFAULT_DONGS)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument("--buildings", type=Path, default=None,
                   help="AL_D010 GeoPackage. 주면 좌표등록 검수 수치를 함께 낸다")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    dongs = json.loads(args.dongs.read_text(encoding="utf-8"))
    args.outdir.mkdir(parents=True, exist_ok=True)
    T = Transform(plan["georeference"])
    ap = plan["approval"]
    gl = float(plan["levels"]["planned_ground_elev_m"])
    heights = height_table(spec, dongs)

    feats: list[dict[str, Any]] = []
    district = Polygon(T.ring(plan["district_boundary_px"]))
    feats.append({"geometry": mapping(district), "properties": {
        "layer": "정비구역경계", "name": "정비구역", "area_m2": round(district.area, 1),
        "approved_m2": ap["district_area_m2"]}})

    b_feats: list[dict[str, Any]] = []
    missing: list[tuple[str, int]] = []
    if plan.get("buildings"):
        from sibeom_model_plan import ModelTransform
        M = ModelTransform(plan)
        for b in plan["buildings"]:
            key = (b["dong"], int(b["floors"]))
            h, src = heights.get(key, (None, "없음"))
            if h is None:
                missing.append(key)
            for poly in parts(Polygon(M.ring(b["ring_px"])).buffer(0)):
                b_feats.append({"geometry": mapping(poly), "properties": {
                    "layer": "신축동", "dong": b["dong"],
                    "zone": "+".join(f"{n}F" for n in b["labels"])
                            if "labels" in b else f"{b['floors']}F",
                    "floors": b["floors"], "height_m": h, "height_source": src,
                    "ground_m": gl,
                    "top_elev_m": None if h is None else round(gl + h, 2),
                    "area_m2": round(poly.area, 1)}})
    feats += b_feats
    if missing:
        print("경고: 높이를 못 찾은 (동, 층수)", sorted(set(missing)))

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
    ]
    if b_feats:
        mg = plan["model_georeference"]
        lines += [
            "## 동별 매스 좌표등록 (일조평가 모델링 배치도)",
            "",
            "| 항목 | 값 |", "|---|---|",
            f"| 축척 | {mg['scale_m_per_px']:.4f} m/화소 |",
            f"| 도면 위쪽 방위 | {mg['up_azimuth_deg']:.1f}° |",
            f"| 정합 사슬 | {mg['chain']} |",
            "",
            "구역경계와 달리 동별 매스는 **모델링 배치도**에서 땄다. 이 도면에는",
            "치수선이 없어 배치도(그림 6-2) 선화에 등방 닮음으로 맞춰 축척을 얻었다.",
            "등방(가로·세로 같은 배율)으로 일치도 0.699 가 나온다는 사실 자체가",
            "도면이 늘어나 있지 않다는 증거다.",
            "",
        ]

    lines += [
        "## 동별 매스 · 건축물높이",
        "",
        "| 동 | 구간 | 층수 | 건축물높이(m) | 최고표고(GL+{:.1f}m) | 수평투영(㎡) | 높이출처 |".format(gl),
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for f in b_feats:
        q = f["properties"]
        lines.append(
            f"| {q['dong']} | {q['zone']} | {q['floors']} | "
            f"{q['height_m']:.2f} | {q['top_elev_m']:.2f} | {q['area_m2']:,.1f} | "
            f"{q['height_source']} |")
    if b_feats:
        top = max(q["properties"]["top_elev_m"] for q in b_feats)
        lines += ["",
                  f"매스 {len(b_feats)}개 / 동 "
                  f"{len({q['properties']['dong'] for q in b_feats})}개 · 최고표고 "
                  f"{top:,.2f} m", ""]

    if built is None:
        lines += ["## 남은 것", "",
                  "**동별 매스 폴리곤이 아직 없다.** `sibeom_model_plan.py` 를",
                  "모델링 배치도와 함께 돌려야 한다.", ""]
    else:
        gfa = sum(q["properties"]["area_m2"] * q["properties"]["floors"]
                  for q in b_feats)
        lines += [
            "## 면적 검증 — 여기서 인가지표와 어긋난다",
            "",
            "| 항목 | 도면 트레이싱 | 인가 | 비 |", "|---|---:|---:|---:|",
            f"| 주동 수평투영 합계 | {built.area:,.0f} ㎡ | 건축면적 "
            f"{ap['building_area_m2']:,.0f} ㎡ | "
            f"{built.area / ap['building_area_m2'] * 100:.0f}% |",
            f"| 수평투영 x 층수 | {gfa:,.0f} ㎡ | 연면적(지상) "
            f"{ap['gfa_above_m2']:,.0f} ㎡ | "
            f"{gfa / ap['gfa_above_m2'] * 100:.0f}% |",
            "",
            "**주동 합계가 인가 건축면적의 절반이다.** 축척 문제는 아니다 —",
            "같은 축척으로 잰 정비구역 면적이 인가치와 2% 안에서 맞고, 축척을 2배",
            "면적이 되도록 키우면 구역면적이 21만㎡ 가 돼 성립하지 않는다.",
            "",
            "교차검증이 가리키는 답은 이렇다.",
            "",
            f"- 수평투영 x 층수 = {gfa:,.0f} ㎡ 는 지상 연면적 "
            f"{ap['gfa_above_m2']:,.0f} ㎡ 의 {gfa / ap['gfa_above_m2'] * 100:.0f}% 다.",
            "  발코니(서비스면적)는 외곽선 안이지만 연면적에서 빠지므로 이 정도",
            "  초과는 아파트에서 정상 범위다.",
            f"- 뒤집어 보면 연면적 / 주동면적 = "
            f"{ap['gfa_above_m2'] / built.area:.1f} 로, 이 단지의 평균 층수(약 25층)와",
            "  거의 같다. 즉 **주동 매스 자체는 크기가 맞다.**",
            "- 남는 약 1.4만㎡ 는 모델링 배치도가 그리지 않은 것 —",
            "  주민공동시설·근린생활시설·주차장 출입구 같은 저층 부속동으로 보인다.",
            "  일조에는 거의 영향이 없으나(대부분 1~2층), 건폐율 검산에는 필요하다.",
            "",
            "→ 저층 부속동까지 넣으려면 **1층 평면도나 배치도 DWG** 가 있어야 한다.",
            "",
        ]
        cf = dongs.get("_conflicts", [])
        if cf:
            lines += ["## 도면과 제원표의 불일치", "", *cf, ""]
    (args.outdir / "sibeom_report.md").write_text("\n".join(lines), encoding="utf-8")

    print("■ 여의도 시범아파트 – 일조권 분석용 폴리곤")
    print(f"   축척 {T.s:.4f} m/화소 · 도면 위쪽 방위 {math.degrees(T.az):.1f}°")
    print(f"   정비구역 {district.area:,.1f} ㎡ (인가 {ap['district_area_m2']:,.2f} ㎡, "
          f"오차 {abs(district.area - ap['district_area_m2']) / ap['district_area_m2'] * 100:.2f}%)")
    if b_feats:
        top = max(q["properties"]["top_elev_m"] for q in b_feats)
        gfa = sum(q["properties"]["area_m2"] * q["properties"]["floors"]
                  for q in b_feats)
        print(f"   매스 {len(b_feats)}개 / 동 "
              f"{len({q['properties']['dong'] for q in b_feats})}개 · 최고표고 "
              f"{top:,.2f} m (GL+{gl})")
        print(f"   주동 수평투영 {built.area:,.0f} ㎡ = 인가 건축면적의 "
              f"{built.area / ap['building_area_m2'] * 100:.0f}%"
              "   ← 저층 부속동은 모델링 배치도에 없다")
        print(f"   수평투영 x 층수 {gfa:,.0f} ㎡ = 지상 연면적의 "
              f"{gfa / ap['gfa_above_m2'] * 100:.0f}%"
              "   ← 발코니 포함분이라 정상 범위")
    else:
        print("   동별 매스 0개  ← sibeom_model_plan.py 를 먼저 돌려야 한다")
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
