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

from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import (
    DAEGYO_JIBUN, HWARANG_JIBUN, apartment_prisms, load_named_buildings,
    resolved_height, school_label, school_parcel_rows,
)
from daegyo_school_hours import dong_labels

SCEN = {"S1": "대교 신축 + 화랑 기존", "S2": "대교 신축 + 화랑 신축(트랙B)"}
DEFAULT_HWARANG = Path("outputs/hwarang_trackB/hwarang_redesign_best.geojson")


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


def build_points(schools: dict[str, list[dict[str, Any]]],
                 buildings: Sequence[dict[str, Any]], spec_all: dict[str, Any],
                 ) -> tuple[list[Point], list[H.Receptor]]:
    """학교별 수광점 — 도면 정의가 있으면 그것, 없으면 일반식."""
    points: list[Point] = []
    receptors: list[H.Receptor] = []
    counter: Counter = Counter()

    def add(rec: Sequence[H.Receptor], school: str, jibun: str, dong: str,
            facade: str, source: str) -> None:
        for r in rec:
            key = (dong, facade, r.floor)
            counter[key] += 1
            face = "" if facade in (dong, "남향외벽") else f" {facade}"
            points.append(Point(
                pid=f"{dong}{face} {r.floor}층-{counter[key]}", school=school,
                jibun=jibun, dong=dong, facade=facade, floor=r.floor,
                idx=counter[key], source=source, x=r.x, y=r.y, z=r.z,
                normal_az=r.normal_az))
            receptors.append(r)

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
                            facade.get("group") or fid, "도면기준")
        rest = [r for r in rows2 if id(r) not in claimed and r["classroom"]]
        for label, row in dong_labels(rest, school):
            rec = H.make_receptors([{**row, "jibun": label}])
            add(rec, school, jibun, label, "남향외벽", "일반식")
    return points, receptors


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
DETAIL_COLS = ["수광점ID", "학교", "지번", "교사동", "수광면", "층", "번호",
               "수광점정의", "X", "Y", "Z", "창면방위",
               "총일조h", "연속h_08_16", "연속h_09_15", "기준A", "기준B"]


def write_detail(path: Path, points: Sequence[Point], code: str) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(DETAIL_COLS)
        for p in points:
            r = p.res[code]
            w.writerow([p.pid, p.school, p.jibun, p.dong, p.facade, p.floor,
                        p.idx, p.source, round(p.x, 2), round(p.y, 2),
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
        w.writerow(["수광점ID", "학교", "교사동", "수광면", "층", "번호",
                    "수광점정의", "X", "Y", "Z",
                    "S1_총일조h", "S1_연속h", "S1_기준A",
                    "S2_총일조h", "S2_연속h", "S2_기준A",
                    "Δ총일조h", "Δ연속h", "변화"])
        for p in points:
            s1, s2 = p.res["S1"], p.res["S2"]
            w.writerow([p.pid, p.school, p.dong, p.facade, p.floor, p.idx,
                        p.source, round(p.x, 2), round(p.y, 2), round(p.z, 2),
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
        f"| 수광점 | **{len(points)}개** (도면기준 {src['도면기준']} / "
        f"일반식 {src['일반식']}) |",
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
        "- 이번 분석은 **교사동 창면 수광점만** 대상이다. 운동장 지반은 들어 있지 않다.",
        "- 여의도중 **체육관동 부속(원형부) 787.5㎡** 는 AL_D010 에 용도·층수·높이가",
        "  모두 비어 종전 분석에서 빠져 있던 동이다. 이번에는 수광점으로도,",
        "  그림자를 만드는 차폐물로도 함께 넣었다.",
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
    points, receptors = build_points(schools, buildings, spec_all)

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
    src = Counter(p.source for p in points)
    print(f"   수광점       {len(points)}개 "
          f"(도면기준 {src['도면기준']} / 일반식 {src['일반식']})")
    for jibun, pts in group_rows(points, lambda p: (p.jibun, p.school)):
        print(f"      {jibun[0]} {jibun[1]:<14} {len(pts):>4}개")

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
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
