#!/usr/bin/env python3
"""수광점의 **자료 근거 등급** – 도면 실측분과 추정분을 갈라 QGIS 로 낸다.

수광점 1,823개가 모두 같은 근거로 만들어진 것이 아니다.

    A 도면기준   사용자가 준 정면도·분석지점도에서 층고와 창 높이를 읽었다.
    B 일반식     도면을 못 받아 층고 3.5m·창중심 1.2m·외벽 5m 등간격으로
                 전부 가정했다.
    C 운동장 근사 실제 운동장 경계가 아니라 옥외지반을 격자로 근사했다.

A 안에도 도면에서 읽은 값과 계측·추정한 값이 섞여 있어, 무엇을 추정했는지
동별로 `추정항목` 에 적어 둔다. 결과를 QGIS 에서 등급별 색으로 볼 수 있게
내보낸다.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import load_named_buildings, school_parcel_rows
from school_receptor_compliance import (
    CRS_5186, build_points, polygon_features, write_geojson,
)

# (지번, 동) → (등급, 근거, 추정항목)
PROVENANCE: dict[tuple[str, str], tuple[str, str, str]] = {
    ("40-3", "본관(4층)"): (
        "A", "본관 정면도",
        "층고 3.6m·창 0.9~3.3m 는 정면도 실측. 수평 배치는 교실모듈 9m·"
        "창 2개소 표준값 가정(정면도가 연속 리본창이라 창 개소를 셀 수 없음)"),
    ("40-3", "별관(5층)"): (
        "A", "별관 정면도",
        "층고 3.2m·창 0.9~3.0m 는 정면도 실측. 수평 배치는 교실모듈 9m 가정. "
        "계단실 제외구간(26~31m, 57~62m)은 정면도에서 눈대중으로 잡은 추정값"),
    ("40-1", "본관(5층)"): (
        "A", "본관 정면도 + 체육관동 배면도 레벨치수",
        "층 바닥레벨(0/3.9/7.5/11.1/14.7)은 도면 치수 실측. 창 1.35~3.45m 는 "
        "정면도 창 띠를 픽셀 계측. 수평 배치는 교실모듈 9m 가정(제외구간 4곳은 "
        "정면도 실측)"),
    ("40-1", "체육관동(정보화센타)"): (
        "A", "체육관동 4면도",
        "Z그리드(0/3.9/7.5/11.1) 도면 실측. 창 1.3~2.8m 는 좌측면도 픽셀 계측. "
        "평면이 꺾여 한 평면으로 안 잡히므로 남향계열(90~270°) 외벽 전체를 "
        "수광면으로 **가정**. 수평 배치는 교실모듈 9m 가정"),
    ("40-1", "체육관동 부속(원형부)"): (
        "A-", "체육관동 4면도(폴리곤 대응은 추론)",
        "AL_D010 에 용도·층수·높이가 모두 결측인 787.5㎡ 동. 체육관동과 2.3m "
        "거리·합계 면적 일치로 **같은 건물의 분할 폴리곤이라고 추론**해 같은 "
        "Z그리드와 창 높이를 적용. 높이 15.0m 도 그 추론에 따른 값"),
    ("40-2", "본관동 저층부(4층)"): (
        "A", "본관동 분석지점도",
        "층고 3.7m 는 분석지점도 층선 간격을 **픽셀 계측**해 AL_D010 실측높이로 "
        "환산. 창 1.1~2.9m 도 분석지점도 창 표기에서 환산. 계단실 폭 4.35m 는 "
        "점 개수가 맞도록 역산한 값. 층별 점 개수는 지침서와 일치 확인"),
    ("40-2", "본관동 고층부(5층)"): (
        "A", "본관동 분석지점도",
        "층고 3.7m·창 1.1~2.9m 는 분석지점도 픽셀 계측. 층별 점 개수(10개/층)는 "
        "지침서와 일치 확인"),
    ("40-2", "A동(3층)"): (
        "A-", "A동 수광지침서",
        "AL_D010 이 5층·13.00m 로 층수와 높이가 서로 모순(5층이면 층고 2.6m). "
        "지침서가 3개층이므로 **3층·층고 4.0m + 파라펫 1.0m 로 추정**. "
        "창 1.5~3.1m 는 창 표기와 층 피치의 비에서 환산해 ±0.2m 오차 가능. "
        "3층 리본창 모듈(면별 7.7m/5.8m)도 점 개수에 맞춘 역산값"),
}

GENERIC_NOTE = (
    "도면 미수령. 층고 3.5m·창중심 바닥+1.2m·남향계열 외벽 전체를 5m 등간격 "
    "으로 분할 — 층고·창높이·수평배치가 **전부 가정값**이다. 도면을 받으면 "
    "위치와 높이가 모두 바뀐다")
GROUND_NOTE = (
    "실제 운동장 경계가 아님. 교사동에서 60m 이내 옥외지반 중 어느 건물에도 "
    "들지 않고 그 학교가 다른 어떤 건물보다 가까운 8m 격자점만 남겨 배정한 "
    "**근사 영역**이다. 실제 운동장 폴리곤을 주면 교체 가능")

GRADE_LABEL = {
    "A": "A 도면기준", "A-": "A− 도면기준(대응·제원 추론 포함)",
    "B": "B 일반식(전부 추정)", "C": "C 운동장 근사",
}

QML_CATS = [
    ("B", "B 일반식(전부 추정)", "214,40,40,255", 3.4),
    ("A-", "A− 도면기준(추론 포함)", "244,162,97,255", 3.0),
    ("C", "C 운동장 근사", "150,150,150,255", 2.0),
    ("A", "A 도면기준", "42,157,143,255", 2.4),
]


def qml_style(attr: str, cats: Sequence[tuple[str, str, str, float]]) -> str:
    cat_xml, sym_xml = [], []
    for i, (value, label, color, size) in enumerate(cats):
        cat_xml.append(f'      <category value="{value}" symbol="{i}" '
                       f'label="{label}" render="true"/>')
        sym_xml.append(
            f'      <symbol type="marker" name="{i}" alpha="1" '
            f'clip_to_extent="1" force_rhr="0">\n'
            f'        <layer class="SimpleMarker" enabled="1" locked="0" pass="0">\n'
            f'          <Option type="Map">\n'
            f'            <Option name="name" type="QString" value="circle"/>\n'
            f'            <Option name="color" type="QString" value="{color}"/>\n'
            f'            <Option name="outline_color" type="QString" '
            f'value="30,30,30,255"/>\n'
            f'            <Option name="outline_width" type="QString" value="0.2"/>\n'
            f'            <Option name="outline_width_unit" type="QString" '
            f'value="MM"/>\n'
            f'            <Option name="size" type="QString" value="{size}"/>\n'
            f'            <Option name="size_unit" type="QString" value="MM"/>\n'
            f'            <Option name="scale_method" type="QString" '
            f'value="diameter"/>\n'
            f'            <Option name="offset" type="QString" value="0,0"/>\n'
            f'            <Option name="angle" type="QString" value="0"/>\n'
            f'          </Option>\n'
            f'        </layer>\n'
            f'      </symbol>')
    return ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
            '<qgis version="3.28.0" styleCategories="Symbology">\n'
            f'  <renderer-v2 type="categorizedSymbol" attr="{attr}" '
            'forceraster="0" symbollevels="0" enableorderby="0">\n'
            "    <categories>\n" + "\n".join(cat_xml) + "\n    </categories>\n"
            "    <symbols>\n" + "\n".join(sym_xml) + "\n    </symbols>\n"
            "  </renderer-v2>\n</qgis>\n")


def classify(p) -> tuple[str, str, str]:
    if p.kind == "운동장 지반":
        return "C", "옥외지반 근사", GROUND_NOTE
    if p.source == "일반식":
        return "B", "일반식(도면 없음)", GENERIC_NOTE
    return PROVENANCE.get((p.jibun, p.dong),
                          ("A", "도면기준", "세부 근거 미기재"))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="수광점 자료 근거 등급")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path,
                   default=Path("outputs/school_compliance/qgis_provenance"))
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--pg-step", type=float, default=8.0)
    p.add_argument("--playground", type=Path, default=None)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    from pyproj import CRS, Transformer
    to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                  always_xy=True)

    buildings = load_named_buildings(args.buildings)
    daegyo = H.load_daegyo(args.daegyo)
    centre = unary_union([p.footprint for p in daegyo]).centroid
    schools = school_parcel_rows(buildings, centre, args.school_radius)
    all_b = unary_union([r["geom"] for r in buildings])
    user_pg: dict[str, Polygon] = {}
    if args.playground and args.playground.exists():
        data = json.loads(args.playground.read_text(encoding="utf-8"))
        for f in data["features"]:
            user_pg[str(f["properties"].get("jibun"))] = shape(f["geometry"])
    points, _rec, grounds = build_points(schools, buildings, SF.load_spec(),
                                         all_b, args.apron, user_pg,
                                         args.pg_step)

    feats: list[dict[str, Any]] = []
    for p in points:
        grade, basis, note = classify(p)
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(p.x, 3),
                                                          round(p.y, 3)]},
            "properties": {
                "pid": p.pid, "school": p.school, "jibun": p.jibun,
                "kind": p.kind, "dong": p.dong, "floor": p.floor,
                "z_m": round(p.z, 2), "normal_az": round(p.normal_az, 1),
                "grade": grade, "근거등급": GRADE_LABEL[grade],
                "근거": basis, "추정항목": note,
                "estimated": grade in ("B", "C"),
            }})

    def emit(stem: str, sel) -> int:
        sub = [f for f in feats if sel(f["properties"])]
        if not sub:
            return 0
        a = args.outdir / f"{stem}_epsg5186.geojson"
        write_geojson(a, sub)
        write_geojson(args.outdir / f"{stem}_wgs84.geojson", sub, to_wgs)
        for q in (a.with_suffix(".qml"),
                  args.outdir / f"{stem}_wgs84.qml"):
            q.write_text(qml_style("grade", QML_CATS), encoding="utf-8")
        return len(sub)

    n_all = emit("수광점_근거등급_전체", lambda q: True)
    n_est = emit("수광점_추정만", lambda q: q["grade"] in ("B", "C"))
    n_b = emit("수광점_추정_일반식", lambda q: q["grade"] == "B")
    n_c = emit("수광점_추정_운동장근사", lambda q: q["grade"] == "C")
    n_a2 = emit("수광점_도면기준_추론포함", lambda q: q["grade"] == "A-")

    pg = polygon_features([(k, v, {"kind": "운동장(근사 영역)"})
                           for k, v in grounds.items()])
    write_geojson(args.outdir / "운동장_근사영역_epsg5186.geojson", pg)
    write_geojson(args.outdir / "운동장_근사영역_wgs84.geojson", pg, to_wgs)

    with (args.outdir / "수광점_근거등급.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["수광점ID", "학교", "지번", "종류", "교사동/운동장", "층",
                    "X", "Y", "Z", "근거등급", "근거", "추정여부", "추정항목"])
        for f in feats:
            q = f["properties"]
            x, y = f["geometry"]["coordinates"]
            w.writerow([q["pid"], q["school"], q["jibun"], q["kind"], q["dong"],
                        q["floor"], x, y, q["z_m"], q["근거등급"], q["근거"],
                        "추정" if q["estimated"] else "도면", q["추정항목"]])

    print("■ 수광점 자료 근거 등급")
    by = Counter((f["properties"]["grade"]) for f in feats)
    for g in ("A", "A-", "B", "C"):
        if by[g]:
            print(f"   {GRADE_LABEL[g]:<28} {by[g]:>5}개")
    print(f"   {'합계':<28} {len(feats):>5}개  "
          f"(추정 B+C = {by['B']+by['C']}개, {(by['B']+by['C'])/len(feats)*100:.0f}%)")

    print("\n■ 동별")
    print(f"{'학교':<14}{'구분':<26}{'등급':<6}{'수광점':>6}  근거")
    seen: dict[tuple, list] = {}
    for f in feats:
        q = f["properties"]
        seen.setdefault((q["jibun"], q["school"], q["dong"]), [0, q])
        seen[(q["jibun"], q["school"], q["dong"])][0] += 1
    for key in sorted(seen):
        n, q = seen[key]
        print(f"{key[1]:<14}{key[2]:<26}{q['grade']:<6}{n:>6}  {q['근거']}")

    print(f"\n레이어 → {args.outdir}")
    print(f"   수광점_근거등급_전체 {n_all} · 수광점_추정만 {n_est} "
          f"(일반식 {n_b} / 운동장근사 {n_c}) · 도면기준_추론포함 {n_a2}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
