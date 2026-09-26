#!/usr/bin/env python3
"""수광점별 학교 일조권 충족여부 — 화랑 재건축 전후 비교.

시나리오
    S1  대교 신축안(사업시행인가) + 화랑 **현재 상태**(기존 10층 5개동)
    S2  대교 신축안              + 화랑 **신축안**(트랙 B · 35층 · 발코니 외형선)

두 시나리오 모두 대교는 '신축안이 지어진 상태'로 고정한다. 따라서 둘의 차이는
**화랑 재건축의 순수 영향**이다.

수광점은 사용자 제공 도면(정면도·분석지점도)으로 정의한 교실 창면을 쓴다
(data/school_facades.json). 도면을 아직 못 받은 학교·동만 종전 일반식으로
생성하며, 출력 표에 '수광점 정의' 열로 구분해 둔다.

판정 기준
    기준A(교육환경평가 일반) 동지일 08~16시 **연속 2시간 이상 또는 총 4시간 이상**
    기준B(강화)              동지일 09~15시 **연속 2시간 이상**
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Point as ShPoint, Polygon, shape
from shapely.ops import unary_union
from shapely.prepared import prep

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import (
    DAEGYO_JIBUN, HWARANG_JIBUN, apartment_prisms, load_named_buildings,
    resolved_height, school_label, school_parcel_rows,
)
import school_playground as PG
from daegyo_school_hours import dong_labels, ground_receptors

SCEN = {"S1": "대교 신축 + 화랑 기존", "S2": "대교 신축 + 화랑 신축(트랙B)"}
DEFAULT_HWARANG = Path("outputs/hwarang_trackB/hwarang_redesign_best.geojson")

CHANGE_CODE = {"유지 충족": "keep_ok", "신규 불충족": "new_fail",
               "신규 충족": "new_ok", "유지 불충족": "keep_fail"}


@dataclass
class Point:
    """수광점 1개와 그 메타데이터."""
    pid: str
    school: str
    jibun: str
    dong: str
    facade: str
    floor: int
    idx: int                      # 같은 (동·면·층) 안에서의 번호
    source: str                   # 도면기준 / 일반식
    kind: str                     # 교사동 창면 / 운동장 지반
    x: float
    y: float
    z: float
    normal_az: float
    res: dict[str, dict[str, Any]] = field(default_factory=dict)


def pass_a(r: dict[str, Any]) -> bool:
    return r["cont_h_08_16"] >= 2.0 or r["total_h_08_16"] >= 4.0


def pass_b(r: dict[str, Any]) -> bool:
    return r["cont_h_09_15"] >= 2.0


def load_plan_prisms(path: Path, label: str) -> list[H.Prism]:
    """설계안 GeoJSON → 프리즘.

    화랑 산출물은 두 가지 형식이 있다.
      · 'line' = '외형선(발코니 끝)' 을 가진 확정 매싱 파일
      · 'label' = '화랑 1동' + height_m 을 가진 재도출(트랙) 파일 — 이 경우
        저장된 폴리곤 자체가 이미 외형선(그림자 형상)이다.
    """
    if not path.exists():
        raise SystemExit(f"설계안 파일을 찾지 못했습니다: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    outline = height = None
    for feature in data["features"]:
        props = feature["properties"]
        if str(props.get("line", "")).startswith("외형선"):
            outline = shape(feature["geometry"])
        if props.get("height_m"):
            height = float(props["height_m"])
            if not props.get("line"):
                outline = shape(feature["geometry"])
    if outline is None or height is None:
        raise SystemExit(f"{path}: 외형선/높이를 읽지 못했습니다.")
    polys = outline.geoms if outline.geom_type == "MultiPolygon" else [outline]
    return [H.Prism(Polygon(p.exterior), height, label) for p in polys]


def school_grounds(sites: dict[str, Any], all_buildings, non_school,
                   apron_m: float, step_m: float,
                   ) -> dict[str, tuple[Any, list[tuple[float, float]]]]:
    """학교별 운동장(옥외지반) 근사 — 지번별로 겹치지 않게 나눈다.

    단순히 '교사동 버퍼 − 건물'로 잡으면 여의도의 네 학교가 서로 붙어 있어
    같은 땅이 여러 학교에 중복으로 잡히고(최대 1.2ha) 도로까지 삼킨다.
    여기서는 격자점 단위로

      · 어느 건물 안에도 들지 않고
      · 가장 가까운 학교까지 apron_m 이내이며
      · **그 학교가 다른 어떤 건물보다 가까운** 점

    만 남겨 가장 가까운 학교에 배정한다. 도로 건너편과 이웃 단지 마당이
    빠지고 학교끼리 중복도 사라진다. 실제 운동장 폴리곤을 --playground 로
    주면 이 근사 대신 그것을 쓴다.
    """
    from shapely.prepared import prep
    from shapely.geometry import Point as ShPoint, box

    region = unary_union([g.buffer(apron_m) for g in sites.values()])
    blocked = prep(all_buildings.buffer(1.0))
    minx, miny, maxx, maxy = region.bounds
    out: dict[str, tuple[Any, list[tuple[float, float]]]] = {
        j: (None, []) for j in sites}
    cells: dict[str, list[Any]] = {j: [] for j in sites}

    y = miny + step_m / 2
    while y <= maxy:
        x = minx + step_m / 2
        while x <= maxx:
            pt = ShPoint(x, y)
            if not blocked.contains(pt):
                best, best_d = None, apron_m
                for jibun, site in sites.items():
                    d = site.distance(pt)
                    if d < best_d:
                        best, best_d = jibun, d
                if best is not None and non_school.distance(pt) >= best_d:
                    out[best][1].append((x, y))
                    cells[best].append(box(x - step_m / 2, y - step_m / 2,
                                           x + step_m / 2, y + step_m / 2))
            x += step_m
        y += step_m
    return {j: (unary_union(cells[j]) if cells[j] else None, pts)
            for j, (_g, pts) in out.items()}


def drop_embedded(points: list[Point], receptors: list[H.Receptor],
                  all_buildings) -> tuple[list[Point], list[H.Receptor], list[Point]]:
    """건물 폴리곤 안에 박힌 창면 점을 골라낸다.

    창면 점은 벽에서 0.4m 띄워 만든다 — 제 건물이든 남의 건물이든, 그 위치가
    어떤 건물 안이라면 뜬 방향이 막혔거나(요철 이빨 옆으로 빠짐) 실제로는
    창이 아니라 계단실·옥탑 같은 부속 구조물 자리라는 뜻이다(정면도가 없는
    일반식 학교, 또는 부속 구조물이 AL_D010에 별도 필지로 잡혀 정면도 판독이
    놓친 경우에 나온다). 운동장 지반 점은 벽 기준이 아니라 대상이 아니다.
    """
    blocked = prep(all_buildings)
    kept_p, kept_r, dropped = [], [], []
    for p, r in zip(points, receptors):
        if p.kind == "교사동 창면" and blocked.contains(ShPoint(p.x, p.y)):
            dropped.append(p)
            continue
        kept_p.append(p)
        kept_r.append(r)
    return kept_p, kept_r, dropped


def build_points(schools: dict[str, list[dict[str, Any]]],
                 buildings: Sequence[dict[str, Any]], spec_all: dict[str, Any],
                 all_buildings, apron_m: float, user_pg: dict[str, Polygon],
                 pg_step_m: float,
                 ) -> tuple[list[Point], list[H.Receptor], dict[str, Polygon], list[Point]]:
    """학교별 수광점 — 교사동 창면 + 운동장 지반.

    반환값 네 번째가 `drop_embedded()` 로 걸러낸 무효점이다 — 몇 개가 어디서
    빠졌는지 호출자가 보고할 수 있도록 남겨 둔다.
    """
    points: list[Point] = []
    receptors: list[H.Receptor] = []
    grounds: dict[str, Polygon] = {}
    counter: Counter = Counter()

    def add(rec: Sequence[H.Receptor], school: str, jibun: str, dong: str,
            facade: str, source: str, kind: str) -> None:
        for r in rec:
            key = (dong, facade, r.floor)
            counter[key] += 1
            if kind == "운동장 지반":
                pid = f"{dong}-{counter[key]}"
            else:
                face = "" if facade in (dong, "남향외벽") else f" {facade}"
                pid = f"{dong}{face} {r.floor}층-{counter[key]}"
            points.append(Point(
                pid=pid, school=school, jibun=jibun, dong=dong, facade=facade,
                floor=r.floor, idx=counter[key], source=source, kind=kind,
                x=r.x, y=r.y, z=r.z, normal_az=r.normal_az))
            receptors.append(r)

    # 운동장 근사는 학교끼리 겹치지 않게 한 번에 나눈다
    sites = {j: unary_union([r["geom"] for r in rows])
             for j, rows in schools.items()}
    non_school = unary_union([r["geom"] for r in buildings
                              if r["jibun"] not in schools])
    approx = school_grounds(sites, all_buildings, non_school, apron_m, pg_step_m)

    for jibun, rows in sorted(schools.items()):
        school = school_label(jibun, rows).split(" ", 1)[1]
        entry = spec_all.get(jibun)
        rows2 = SF.augment_rows(jibun, rows, buildings, entry) if entry else rows
        claimed: set[int] = set()
        if entry:
            for spec in entry["buildings"]:
                row = SF.find_row(rows2, spec)
                if row is None:
                    continue
                claimed.add(id(row))
                for i, facade in enumerate(SF.spec_facades(spec)):
                    fid = SF.facade_id(spec, facade, i)
                    rec = SF.receptors_from_facade(row, spec, facade, fid)
                    if rec:
                        add(rec, school, jibun, spec["id"],
                            facade.get("group") or fid, "도면기준", "교사동 창면")
        rest = [r for r in rows2 if id(r) not in claimed and r["classroom"]]
        for label, row in dong_labels(rest, school):
            rec = H.make_receptors([{**row, "jibun": label}])
            add(rec, school, jibun, label, "남향외벽", "일반식", "교사동 창면")

        # 운동장 지반(수평면)
        label = f"{school} 운동장"
        pg_spec = PG.load_spec()
        if jibun in user_pg:
            pg = user_pg[jibun]
            rec = ground_receptors(pg, label, pg_step_m)
            src = "사용자 폴리곤"
        elif jibun in pg_spec:
            # 분석지점도 격자가 있는 학교 – 칸마다 한 점
            area, _xy = approx[jibun]
            if area is None:
                continue
            pg, rec, basis, _ = PG.build(jibun, area, pg_spec, label, pg_step_m)
            src = basis
        else:
            pg, xy = approx[jibun]
            rec = [H.Receptor(x, y, 0.0, 180.0, label, 0, True) for x, y in xy]
            src = "옥외지반 근사"
        if pg is None or not rec:
            continue
        grounds[label] = pg
        add(rec, school, jibun, label, "지반", src, "운동장 지반")

    points, receptors, dropped = drop_embedded(points, receptors, all_buildings)
    return points, receptors, grounds, dropped


def build_context(buildings: Sequence[dict[str, Any]], centre, radius: float,
                  spec_all: dict[str, Any]) -> tuple[list[H.Prism], int]:
    """차폐물 — 대교·화랑 대지 밖의 모든 기존 건물.

    AL_D010 에 용도·층수·높이가 모두 비어 종전 분석에서 통째로 빠져 있던 동은
    도면으로 복원한 높이(school_facades.json 의 height_m)로 살려 넣는다.
    여의도중 체육관동 부속 787.5㎡ 가 여기 해당한다.
    """
    recovered: dict[tuple[str, int], float] = {}
    for jibun, entry in spec_all.items():
        for spec in entry.get("buildings", []):
            if spec.get("height_m"):
                m = spec.get("match", {})
                if "area_m2" in m:
                    recovered[(jibun, round(m["area_m2"]))] = float(spec["height_m"])

    context: list[H.Prism] = []
    n_recovered = 0
    for row in buildings:
        if row["jibun"] in (DAEGYO_JIBUN, HWARANG_JIBUN):
            continue
        if row["geom"].centroid.distance(centre) > radius:
            continue
        height = resolved_height(row)
        if height <= 0.0:
            height = recovered.get((row["jibun"], round(row["geom"].area)), 0.0)
            if height <= 0.0:
                continue
            n_recovered += 1
        geom = row["geom"]
        for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
            context.append(H.Prism(poly, height, "기존건물"))
    return context, n_recovered


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
DETAIL_COLS = ["수광점ID", "학교", "지번", "종류", "교사동/운동장", "수광면", "층",
               "번호", "수광점정의", "X", "Y", "Z", "창면방위",
               "총일조h", "연속h_08_16", "연속h_09_15", "기준A", "기준B"]


def write_detail(path: Path, points: Sequence[Point], code: str) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(DETAIL_COLS)
        for p in points:
            r = p.res[code]
            w.writerow([p.pid, p.school, p.jibun, p.kind, p.dong, p.facade,
                        p.floor, p.idx, p.source, round(p.x, 2), round(p.y, 2),
                        round(p.z, 2), round(p.normal_az, 1),
                        r["total_h_08_16"], r["cont_h_08_16"], r["cont_h_09_15"],
                        "충족" if pass_a(r) else "불충족",
                        "충족" if pass_b(r) else "불충족"])


def change_class(p: Point) -> str:
    a, b = pass_a(p.res["S1"]), pass_a(p.res["S2"])
    if a and b:
        return "유지 충족"
    if a and not b:
        return "신규 불충족"
    if not a and b:
        return "신규 충족"
    return "유지 불충족"


def write_compare(path: Path, points: Sequence[Point]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["수광점ID", "학교", "종류", "교사동/운동장", "수광면", "층",
                    "번호", "수광점정의", "X", "Y", "Z",
                    "S1_총일조h", "S1_연속h", "S1_기준A",
                    "S2_총일조h", "S2_연속h", "S2_기준A",
                    "Δ총일조h", "Δ연속h", "변화"])
        for p in points:
            s1, s2 = p.res["S1"], p.res["S2"]
            w.writerow([p.pid, p.school, p.kind, p.dong, p.facade, p.floor,
                        p.idx, p.source, round(p.x, 2), round(p.y, 2),
                        round(p.z, 2),
                        s1["total_h_08_16"], s1["cont_h_08_16"],
                        "충족" if pass_a(s1) else "불충족",
                        s2["total_h_08_16"], s2["cont_h_08_16"],
                        "충족" if pass_a(s2) else "불충족",
                        round(s2["total_h_08_16"] - s1["total_h_08_16"], 2),
                        round(s2["cont_h_08_16"] - s1["cont_h_08_16"], 2),
                        change_class(p)])


def group_rows(points: Sequence[Point], key) -> list[tuple[Any, list[Point]]]:
    out: dict[Any, list[Point]] = defaultdict(list)
    for p in points:
        out[key(p)].append(p)
    return sorted(out.items(), key=lambda kv: str(kv[0]))


def summary(group: Sequence[Point], code: str) -> dict[str, float]:
    n = len(group)
    ok = sum(1 for p in group if pass_a(p.res[code]))
    okb = sum(1 for p in group if pass_b(p.res[code]))
    return {
        "n": n, "pass": ok, "pct": ok / n * 100.0,
        "pass_b": okb, "pct_b": okb / n * 100.0,
        "mean_total": sum(p.res[code]["total_h_08_16"] for p in group) / n,
        "mean_cont": sum(p.res[code]["cont_h_08_16"] for p in group) / n,
    }


def print_step(points: Sequence[Point], code: str) -> None:
    title = f"[{code}] {SCEN[code]} — 수광점별 일조권 충족여부"
    print("\n" + "=" * 104)
    print(title)
    print("=" * 104)
    print(f"{'학교 / 교사동':<34}{'정의':<7}{'수광점':>6}{'기준A 충족':>11}"
          f"{'충족률':>9}{'기준B 충족':>11}{'평균 총일조':>12}{'평균 연속':>11}")
    print("-" * 104)
    for school, pts in group_rows(points, lambda p: (p.jibun, p.school)):
        s = summary(pts, code)
        print(f"{school[0]+' '+school[1]:<34}{'':<7}{s['n']:>6}"
              f"{s['pass']:>11}{s['pct']:>8.1f}%{s['pass_b']:>11}"
              f"{s['mean_total']:>11.2f}h{s['mean_cont']:>10.2f}h")
        for dong, dpts in group_rows(pts, lambda p: p.dong):
            d = summary(dpts, code)
            src = dpts[0].source
            print(f"{'  └ '+str(dong):<34}{src:<7}{d['n']:>6}"
                  f"{d['pass']:>11}{d['pct']:>8.1f}%{d['pass_b']:>11}"
                  f"{d['mean_total']:>11.2f}h{d['mean_cont']:>10.2f}h")
    t = summary(points, code)
    print("-" * 104)
    print(f"{'전체':<34}{'':<7}{t['n']:>6}{t['pass']:>11}{t['pct']:>8.1f}%"
          f"{t['pass_b']:>11}{t['mean_total']:>11.2f}h{t['mean_cont']:>10.2f}h")


def print_compare(points: Sequence[Point]) -> None:
    print("\n" + "=" * 112)
    print("[비교] 화랑 재건축 전(S1) → 후(S2) 수광점별 충족여부 변화 "
          "(대교는 두 시나리오 모두 신축안)")
    print("=" * 112)
    print(f"{'학교 / 교사동':<34}{'수광점':>6}{'S1 충족':>9}{'S2 충족':>9}"
          f"{'충족률 변화':>13}{'신규불충족':>11}{'신규충족':>10}"
          f"{'평균 총일조 변화':>17}")
    print("-" * 112)

    def line(label: str, pts: Sequence[Point]) -> None:
        a, b = summary(pts, "S1"), summary(pts, "S2")
        c = Counter(change_class(p) for p in pts)
        print(f"{label:<34}{a['n']:>6}{a['pass']:>9}{b['pass']:>9}"
              f"{b['pct']-a['pct']:>+12.1f}%p{c['신규 불충족']:>11}"
              f"{c['신규 충족']:>10}{b['mean_total']-a['mean_total']:>+16.2f}h")

    for school, pts in group_rows(points, lambda p: (p.jibun, p.school)):
        line(school[0] + " " + school[1], pts)
        for dong, dpts in group_rows(pts, lambda p: p.dong):
            line("  └ " + str(dong), dpts)
    print("-" * 112)
    line("전체", points)

    flips = [p for p in points if change_class(p) == "신규 불충족"]
    gains = [p for p in points if change_class(p) == "신규 충족"]
    print(f"\n충족 → 불충족으로 바뀌는 수광점 {len(flips)}개"
          f" / 불충족 → 충족 {len(gains)}개")
    if flips:
        print("-" * 112)
        print(f"{'수광점ID':<34}{'학교':<14}{'층':>3}{'S1 연속':>9}{'S1 총':>8}"
              f"{'S2 연속':>9}{'S2 총':>8}{'Δ총':>8}")
        for p in sorted(flips, key=lambda q: q.res["S2"]["total_h_08_16"]):
            s1, s2 = p.res["S1"], p.res["S2"]
            print(f"{p.pid:<34}{p.school:<14}{p.floor:>3}"
                  f"{s1['cont_h_08_16']:>8.2f}h{s1['total_h_08_16']:>7.2f}h"
                  f"{s2['cont_h_08_16']:>8.2f}h{s2['total_h_08_16']:>7.2f}h"
                  f"{s2['total_h_08_16']-s1['total_h_08_16']:>+7.2f}h")


# --------------------------------------------------------------------------- #
# QGIS 산출물
# --------------------------------------------------------------------------- #
CRS_5186 = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}}

QML_CATEGORIES = [
    ("new_fail", "신규 불충족", "214,40,40,255", 3.6),
    ("keep_fail", "유지 불충족", "150,150,150,255", 2.0),
    ("new_ok", "신규 충족", "29,101,181,255", 2.8),
    ("keep_ok", "유지 충족", "120,170,120,255", 1.8),
]


def qml_style(attr: str) -> str:
    """QGIS 3 레이어 스타일(.qml) — 변화 구분 기준 분류 심볼."""
    cats, syms = [], []
    for i, (value, label, color, size) in enumerate(QML_CATEGORIES):
        cats.append(f'      <category value="{value}" symbol="{i}" '
                    f'label="{label}" render="true"/>')
        syms.append(
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
            "    <categories>\n" + "\n".join(cats) + "\n    </categories>\n"
            "    <symbols>\n" + "\n".join(syms) + "\n    </symbols>\n"
            "  </renderer-v2>\n</qgis>\n")


def point_feature(p: Point) -> dict[str, Any]:
    s1, s2 = p.res["S1"], p.res["S2"]
    chg = change_class(p)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(p.x, 3),
                                                      round(p.y, 3)]},
        "properties": {
            "pid": p.pid, "school": p.school, "jibun": p.jibun, "kind": p.kind,
            "dong": p.dong, "facade": p.facade, "floor": p.floor,
            "source": p.source, "z_m": round(p.z, 2),
            "normal_az": round(p.normal_az, 1),
            "s1_total_h": s1["total_h_08_16"], "s1_cont_h": s1["cont_h_08_16"],
            "s1_pass_a": bool(pass_a(s1)), "s1_pass_b": bool(pass_b(s1)),
            "s2_total_h": s2["total_h_08_16"], "s2_cont_h": s2["cont_h_08_16"],
            "s2_pass_a": bool(pass_a(s2)), "s2_pass_b": bool(pass_b(s2)),
            "d_total_h": round(s2["total_h_08_16"] - s1["total_h_08_16"], 2),
            "d_cont_h": round(s2["cont_h_08_16"] - s1["cont_h_08_16"], 2),
            "change_cd": CHANGE_CODE[chg], "변화": chg,
        },
    }


def write_geojson(path: Path, features: Sequence[dict[str, Any]],
                  to_wgs84=None) -> None:
    feats = features
    if to_wgs84 is not None:
        feats = []
        for f in features:
            g = dict(f["geometry"])
            if g["type"] == "Point":
                g["coordinates"] = [round(v, 8) for v in
                                    to_wgs84.transform(*g["coordinates"])]
            else:
                g["coordinates"] = [
                    [[round(v, 8) for v in to_wgs84.transform(x, y)]
                     for x, y in ring] for ring in g["coordinates"]]
            feats.append({**f, "geometry": g})
    doc: dict[str, Any] = {"type": "FeatureCollection", "features": feats}
    if to_wgs84 is None:
        doc["crs"] = CRS_5186
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                    encoding="utf-8")


def polygon_features(items: Sequence[tuple[str, Any, dict[str, Any]]]
                     ) -> list[dict[str, Any]]:
    out = []
    for label, geom, props in items:
        for poly in (geom.geoms if geom.geom_type == "MultiPolygon" else [geom]):
            rings = [[[round(x, 3), round(y, 3)] for x, y in poly.exterior.coords]]
            rings += [[[round(x, 3), round(y, 3)] for x, y in i.coords]
                      for i in poly.interiors]
            out.append({"type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": rings},
                        "properties": {"label": label, **props}})
    return out


def write_qgis(outdir: Path, points: Sequence[Point], grounds: dict[str, Polygon],
               daegyo_new, hwarang_now, hwarang_new, context) -> list[Path]:
    """QGIS 에서 바로 열어 볼 수 있는 레이어 일체."""
    from pyproj import CRS, Transformer
    to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                  always_xy=True)
    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    def emit(stem: str, feats, style_attr: str | None = None) -> None:
        a = outdir / f"{stem}_epsg5186.geojson"
        b = outdir / f"{stem}_wgs84.geojson"
        write_geojson(a, feats)
        write_geojson(b, feats, to_wgs)
        written.extend([a, b])
        if style_attr:
            for target in (a, b):
                qml = target.with_suffix(".qml")
                qml.write_text(qml_style(style_attr), encoding="utf-8")
                written.append(qml)

    feats = [point_feature(p) for p in points]
    emit("수광점_전체", feats, "change_cd")
    emit("수광점_신규불충족",
         [f for f in feats if f["properties"]["change_cd"] == "new_fail"],
         "change_cd")
    emit("수광점_신규충족",
         [f for f in feats if f["properties"]["change_cd"] == "new_ok"],
         "change_cd")
    emit("운동장_지반", polygon_features(
        [(k, v, {"kind": "운동장(옥외지반 근사)"}) for k, v in grounds.items()]))
    emit("매싱", polygon_features(
        [(p.label, p.footprint, {"group": "대교 신축안",
                                 "height_m": round(p.top_m, 2)})
         for p in daegyo_new]
        + [(p.label, p.footprint, {"group": "화랑 기존",
                                   "height_m": round(p.top_m, 2)})
           for p in hwarang_now]
        + [(p.label, p.footprint, {"group": "화랑 신축안(트랙B)",
                                   "height_m": round(p.top_m, 2)})
           for p in hwarang_new]))
    emit("차폐물_기존건물", polygon_features(
        [(p.label, p.footprint, {"height_m": round(p.top_m, 2)})
         for p in context]))
    return written


def md_table(points: Sequence[Point], code: str | None) -> list[str]:
    """학교·교사동별 표 — code 가 None 이면 전후 비교표."""
    if code:
        out = ["| 학교 / 교사동 | 수광점 정의 | 수광점 | 기준A 충족 | 충족률 | "
               "기준B 충족 | 평균 총일조 | 평균 연속 |",
               "|---|---|---:|---:|---:|---:|---:|---:|"]
    else:
        out = ["| 학교 / 교사동 | 수광점 | S1 충족 | S2 충족 | 충족률 변화 | "
               "신규 불충족 | 신규 충족 | 평균 총일조 변화 |",
               "|---|---:|---:|---:|---:|---:|---:|---:|"]

    def row(label: str, pts: Sequence[Point], bold: bool) -> str:
        b = "**" if bold else ""
        if code:
            s = summary(pts, code)
            src = pts[0].source if not bold else "–"
            return (f"| {b}{label}{b} | {src} | {s['n']} | {s['pass']} | "
                    f"{b}{s['pct']:.1f}%{b} | {s['pass_b']} | "
                    f"{s['mean_total']:.2f}h | {s['mean_cont']:.2f}h |")
        a, t = summary(pts, "S1"), summary(pts, "S2")
        c = Counter(change_class(p) for p in pts)
        return (f"| {b}{label}{b} | {a['n']} | {a['pass']} | {t['pass']} | "
                f"{b}{t['pct']-a['pct']:+.1f}%p{b} | {c['신규 불충족']} | "
                f"{c['신규 충족']} | {t['mean_total']-a['mean_total']:+.2f}h |")

    for school, pts in group_rows(points, lambda p: (p.jibun, p.school)):
        out.append(row(f"{school[0]} {school[1]}", pts, True))
        for dong, dpts in group_rows(pts, lambda p: p.dong):
            out.append(row(f"　└ {dong}", dpts, False))
    out.append(row("전체", points, True))
    return out


def write_report(path: Path, points: Sequence[Point], args,
                 n_times: int, n_context: int, n_recovered: int,
                 hwarang_new: Sequence[H.Prism]) -> None:
    src = Counter(p.source for p in points)
    kinds = Counter(p.kind for p in points)
    flips = sorted((p for p in points if change_class(p) == "신규 불충족"),
                   key=lambda q: q.res["S2"]["total_h_08_16"])
    gains = [p for p in points if change_class(p) == "신규 충족"]
    lines = [
        "# 수광점별 학교 일조권 충족여부 — 화랑 재건축 전후",
        "",
        "## 분석 조건",
        "",
        "| 항목 | 내용 |",
        "|---|---|",
        f"| S1 (전) | 대교 **신축안**(사업시행인가) + 화랑 **현재 상태**(10층 5개동) |",
        f"| S2 (후) | 대교 **신축안** + 화랑 **신축안**(트랙 B · 35층 · 107.2m · "
        f"외형선 {sum(p.footprint.area for p in hwarang_new):,.0f}㎡) |",
        f"| 기준일 | {args.date}(동지) 08~16시 · {args.step_min}분 간격 "
        f"{n_times}개 시점 |",
        "| 기준A | 연속 2시간 이상 **또는** 총 4시간 이상 (교육환경평가 일반) |",
        "| 기준B | 09~15시 연속 2시간 이상 (강화) |",
        f"| 수광점 | **{len(points)}개** = 교사동 창면 {kinds['교사동 창면']} + "
        f"운동장 지반 {kinds['운동장 지반']} |",
        f"| 교사동 창면 | 도면기준 {src['도면기준']} / 일반식 {src['일반식']} |",
        f"| 운동장 지반 | {args.pg_step:g}m 격자 · 수평면(창면 방위 제약 없음) · "
        f"{'사용자 폴리곤' if src['사용자 폴리곤'] else f'옥외지반 근사(apron {args.apron:g}m)'} |",
        f"| 주변 차폐물 | {n_context}개 (이 중 {n_recovered}개는 AL_D010 속성 "
        f"결측분을 도면 높이로 복원) |",
        "",
        "두 시나리오 모두 **대교는 신축안이 지어진 상태**로 고정했다. 따라서 둘의",
        "차이는 **화랑 재건축만의 순수 영향**이다.",
        "",
        "---",
        "",
        "## 1. S1 — 대교 신축 + 화랑 기존",
        "",
    ]
    lines += md_table(points, "S1")
    lines += ["", "전체 수광점 CSV: `outputs/school_compliance/"
              "step1_대교신축_화랑기존.csv`", "", "---", "",
              "## 2. S2 — 대교 신축 + 화랑 신축(트랙 B)", ""]
    lines += md_table(points, "S2")
    lines += ["", "전체 수광점 CSV: `outputs/school_compliance/"
              "step2_대교신축_화랑신축.csv`", "", "---", "",
              "## 3. 전후 비교", ""]
    lines += md_table(points, None)
    lines += [
        "",
        f"충족 → 불충족으로 바뀌는 수광점 **{len(flips)}개**, "
        f"불충족 → 충족 **{len(gains)}개**.",
        "",
        "전체 수광점 CSV: `outputs/school_compliance/step3_전후비교.csv`",
        "",
        "### 3.1 신규 불충족 수광점",
        "",
        "| 수광점 | 학교 | 층 | S1 연속 | S1 총 | S2 연속 | S2 총 | Δ총 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in flips:
        s1, s2 = p.res["S1"], p.res["S2"]
        lines.append(
            f"| {p.pid} | {p.school} | {p.floor} | {s1['cont_h_08_16']:.2f}h | "
            f"{s1['total_h_08_16']:.2f}h | {s2['cont_h_08_16']:.2f}h | "
            f"{s2['total_h_08_16']:.2f}h | "
            f"{s2['total_h_08_16']-s1['total_h_08_16']:+.2f}h |")
    lines += [
        "",
        "### 3.2 신규 충족 수광점",
        "",
        "| 수광점 | 학교 | 층 | S1 연속 | S1 총 | S2 연속 | S2 총 | Δ총 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for p in sorted(gains, key=lambda q: -q.res["S2"]["total_h_08_16"]):
        s1, s2 = p.res["S1"], p.res["S2"]
        lines.append(
            f"| {p.pid} | {p.school} | {p.floor} | {s1['cont_h_08_16']:.2f}h | "
            f"{s1['total_h_08_16']:.2f}h | {s2['cont_h_08_16']:.2f}h | "
            f"{s2['total_h_08_16']:.2f}h | "
            f"{s2['total_h_08_16']-s1['total_h_08_16']:+.2f}h |")
    lines += [
        "",
        "---",
        "",
        "## 4. 이 표를 읽을 때 주의할 점",
        "",
        "- **충족률(%)은 문턱 지표다.** 여유 있는 수광점이 시간을 잃어도 문턱을",
        "  넘는 한 표에 안 잡힌다. 그래서 충족률과 평균 일조시간을 같이 실었다.",
        f"- **{src['일반식']}개 수광점은 아직 일반식**이다(여의도고 전체, 여의도여고",
        "  B동·②동). 도면을 받으면 위치·층고·창높이가 달라져 수치가 바뀐다.",
        "  특히 여의도고는 전체 수광점의 약 1/4을 차지해 영향이 크다.",
        "- **운동장은 실제 폴리곤이 아니라 근사다.** 교사동에서 apron 이내의",
        "  옥외지반 중, 어느 건물에도 들지 않고 그 학교가 다른 어떤 건물보다 가까운",
        "  격자점만 남겨 가장 가까운 학교에 배정했다(학교끼리 중복 없음). 실제",
        "  운동장 경계를 `--playground` 로 주면 그것을 쓴다.",
        "- 여의도중 **체육관동 부속(원형부) 787.5㎡** 는 AL_D010 에 용도·층수·높이가",
        "  모두 비어 종전 분석에서 빠져 있던 동이다. 이번에는 수광점으로도,",
        "  그림자를 만드는 차폐물로도 함께 넣었다.",
        "",
        "---",
        "",
        "## 5. QGIS 산출물",
        "",
        "`outputs/school_compliance/qgis/` 에 레이어를 넣었다. 파일마다",
        "`_epsg5186`(원본 좌표계)와 `_wgs84`(경위도) 두 벌이 있다.",
        "",
        "| 레이어 | 내용 |",
        "|---|---|",
        "| `수광점_전체` | 수광점 전량. S1·S2 일조시간·충족여부와 변화 구분이 속성 |",
        "| `수광점_신규불충족` | **충족 → 불충족으로 바뀌는 점만** |",
        "| `수광점_신규충족` | 불충족 → 충족으로 바뀌는 점만 |",
        "| `운동장_지반` | 실제로 평가에 쓴 운동장 근사 폴리곤 |",
        "| `매싱` | 대교 신축안 · 화랑 기존 · 화랑 신축안 외형선 |",
        "| `차폐물_기존건물` | 그림자를 만드는 주변 기존 건물 |",
        "",
        "수광점 레이어에는 같은 이름의 `.qml` 이 함께 있어, QGIS 에서 열면",
        "`change_cd` 기준으로 색이 자동 적용된다(신규 불충족 = 빨강·큰 점).",
        "속성 주요 필드:",
        "",
        "`pid` 수광점ID · `school` 학교 · `kind` 창면/지반 · `dong` 동 ·",
        "`floor` 층 · `source` 수광점 정의 · `s1_total_h`/`s1_cont_h`/`s1_pass_a` ·",
        "`s2_total_h`/`s2_cont_h`/`s2_pass_a` · `d_total_h`/`d_cont_h` 변화량 ·",
        "`change_cd`(keep_ok/new_fail/new_ok/keep_fail) · `변화`(한글)",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="수광점별 학교 일조권 충족여부")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--hwarang", type=Path, default=DEFAULT_HWARANG)
    p.add_argument("--outdir", type=Path, default=Path("outputs/school_compliance"))
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--playground", type=Path, default=None,
                   help="실제 운동장 폴리곤 GeoJSON(properties.jibun 필요). "
                        "없으면 교사동 주변 옥외지반으로 근사한다.")
    p.add_argument("--apron", type=float, default=60.0,
                   help="운동장 근사 시 교사동에서 띄우는 거리(m)")
    p.add_argument("--pg-step", type=float, default=8.0,
                   help="운동장 지반 격자 간격(m)")
    p.add_argument("--steps", type=str, default="1,2,3",
                   help="실행할 단계(1=화랑 기존, 2=화랑 신축, 3=비교)")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    steps = {s.strip() for s in args.steps.split(",")}
    from pyproj import CRS, Transformer

    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)
    daegyo_new = H.load_daegyo(args.daegyo)
    centre = unary_union([p.footprint for p in daegyo_new]).centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)

    spec_all = SF.load_spec()
    schools = school_parcel_rows(buildings, centre, args.school_radius)
    all_buildings = unary_union([r["geom"] for r in buildings])
    user_pg: dict[str, Polygon] = {}
    if args.playground and args.playground.exists():
        data = json.loads(args.playground.read_text(encoding="utf-8"))
        for f in data["features"]:
            key = str(f["properties"].get("jibun")
                      or f["properties"].get("school"))
            user_pg[key] = shape(f["geometry"])
    points, receptors, grounds, dropped = build_points(
        schools, buildings, spec_all, all_buildings, args.apron, user_pg,
        args.pg_step)
    if dropped:
        print(f"■ 건물 폴리곤 안에 박혀 제외한 창면 점 {len(dropped)}개 "
              "(요철 이빨 간섭 또는 부속 구조물 자리 — 정면도 확인 권장)")
        for jibun, group in group_rows(dropped, lambda p: (p.jibun, p.dong)):
            print(f"   {jibun[0]} {jibun[1]:<22} {len(group)}개")
        print()

    hwarang_now = apartment_prisms(buildings, HWARANG_JIBUN, "화랑 기존")
    hwarang_new = load_plan_prisms(args.hwarang, "화랑 신축(트랙B)")
    context, n_recovered = build_context(buildings, centre, args.context_radius,
                                         spec_all)

    print("■ 입력")
    print(f"   기준일 {args.date}(동지) 08~16시 {args.step_min}분 간격 "
          f"{len(times)}개 시점")
    print(f"   대교 신축안  프리즘 {len(daegyo_new)}개 "
          f"(최고 {max(p.top_m for p in daegyo_new):.1f}m)")
    print(f"   화랑 기존    프리즘 {len(hwarang_now)}개 "
          f"(최고 {max(p.top_m for p in hwarang_now):.1f}m)")
    print(f"   화랑 신축안  프리즘 {len(hwarang_new)}개 "
          f"(최고 {max(p.top_m for p in hwarang_new):.1f}m, "
          f"footprint {sum(p.footprint.area for p in hwarang_new):.0f}㎡ = 외형선)")
    print(f"   주변 차폐물  {len(context)}개 "
          f"(이 중 {n_recovered}개는 AL_D010 속성 결측분을 도면 높이로 복원)")
    kinds = Counter(p.kind for p in points)
    src = Counter(p.source for p in points)
    print(f"   수광점       {len(points)}개 "
          f"(교사동 창면 {kinds['교사동 창면']} / 운동장 지반 "
          f"{kinds['운동장 지반']}) · 도면기준 {src['도면기준']} / "
          f"일반식 {src['일반식']}")
    for jibun, pts in group_rows(points, lambda p: (p.jibun, p.school)):
        k = Counter(q.kind for q in pts)
        print(f"      {jibun[0]} {jibun[1]:<14} {len(pts):>4}개 "
              f"(창면 {k['교사동 창면']:>3} / 지반 {k['운동장 지반']:>3})")

    masks = H.build_context_masks(context, receptors, times)

    combos = {"S1": daegyo_new + hwarang_now, "S2": daegyo_new + hwarang_new}
    for code, prisms in combos.items():
        res = H.evaluate(receptors, prisms, times, masks, args.step_min)
        for p, r in zip(points, res):
            p.res[code] = r
        print(f"\n   평가 완료: {code} = {SCEN[code]}")

    if "1" in steps:
        print_step(points, "S1")
        out = args.outdir / "step1_대교신축_화랑기존.csv"
        write_detail(out, points, "S1")
        print(f"\n   → {out}")
    if "2" in steps:
        print_step(points, "S2")
        out = args.outdir / "step2_대교신축_화랑신축.csv"
        write_detail(out, points, "S2")
        print(f"\n   → {out}")
    if "3" in steps:
        print_compare(points)
        out = args.outdir / "step3_전후비교.csv"
        write_compare(out, points)
        print(f"\n   → {out}")
    if steps >= {"1", "2", "3"}:
        rep = Path("docs/school_receptor_compliance.md")
        write_report(rep, points, args, len(times), len(context), n_recovered,
                     hwarang_new)
        print(f"   → {rep}")
        qdir = args.outdir / "qgis"
        files = write_qgis(qdir, points, grounds, daegyo_new, hwarang_now,
                           hwarang_new, context)
        print(f"\n■ QGIS 레이어 {len(files)}개 → {qdir}")
        for f in sorted({x.stem for x in files if x.suffix == ".geojson"}):
            print(f"   {f}.geojson")
        print("   * _epsg5186 = 좌표계 EPSG:5186(원본), _wgs84 = 경위도")
        print("   * 같은 이름의 .qml 이 있으면 QGIS 가 '변화' 구분 색을 자동 적용")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
