#!/usr/bin/env python3
"""대교·시범 신축 + 화랑 3가지 안 — 학교 일조 3안 비교.

대교와 시범은 두 안 모두에서 **신축안**으로 고정하고, 화랑만 바꿔 가며
학교 수광점 충족/불충족을 비교한다.

    A. 대교 신축 + 시범 신축 + 화랑 **현재상태**(기존 10층 3개동)
    B. 대교 신축 + 시범 신축 + 화랑 **신축안(고층)** — 41층+저층부 7층,
       종전 검토(`outputs/hwarang_2026`)의 신규 불충족 최소 최적안
    C. 대교 신축 + 시범 신축 + 화랑 **신축안(20층 이하)** — 20층 1개동,
       종전 검토(`outputs/hwarang_20f_multi_2026`)의 건폐율 최소 최적안

A가 '화랑을 아직 안 건드린' 기준선이므로, **B/A·C/A 비교가 화랑
재건축 자체의 순수한 영향**이다(대교·시범 효과는 세 안에 똑같이 깔려
있어 상쇄된다). B/C 비교는 고층안과 20층 이하안 중 어느 쪽이 학교에
더 나은지를 직접 answer한다.

고층안(B)은 층고 2.95m(그 검토 당시 기본값), 20층 이하안(C)은 층고
3.3m(그 검토에서 지정한 값)를 그대로 쓴다 — 각각 확정된 산출물의
치수이지 이 스크립트가 새로 계산하지 않는다.

    python3 hwarang_daegyo_sibeom_scenarios.py --buildings <AL_D010.gpkg>
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import hwarang_massing_study as H
from apartment_attribution import load_prisms_from_geojson, setup as aa_setup
from school_receptor_compliance import (
    CHANGE_CODE, pass_a, polygon_features, qml_style, write_geojson,
)

SCENARIOS = {
    "A_화랑현재": ("대교신축+시범신축+화랑현재상태", "hwarang"),
    "B_화랑고층안": ("대교신축+시범신축+화랑신축(고층)", None),
    "C_화랑20층이하안": ("대교신축+시범신축+화랑신축(20층이하)", None),
}
HIGHRISE_GEOJSON = Path("outputs/hwarang_2026/최적안_매싱_epsg5186.geojson")
LOWRISE_GEOJSON = Path("outputs/hwarang_20f_multi_2026/최적안_매싱_epsg5186.geojson")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--sibeom", type=Path,
                   default=Path("outputs/sibeom/sibeom_epsg5186.geojson"))
    p.add_argument("--hwarang-highrise", type=Path, default=HIGHRISE_GEOJSON)
    p.add_argument("--hwarang-lowrise", type=Path, default=LOWRISE_GEOJSON)
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_3scenarios_2026"))
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--pg-step", type=float, default=8.0)
    return p.parse_args(argv)


def school_table(ctx: dict[str, Any], resA, resB, keyB: str
                 ) -> list[list[Any]]:
    """학교·동별 [구분, 수광점, A충족, B충족, 신규불충족, 신규충족, 시간변화]."""
    agg: dict[str, list] = defaultdict(lambda: [0, 0, 0, 0, 0, 0.0])
    for p, ra, rb in zip(ctx["points"], resA, resB):
        okA, okB = pass_a(ra), pass_a(rb)
        a = agg[f"{p.school} / {p.dong}"]
        a[0] += 1
        a[1] += int(okA)
        a[2] += int(okB)
        a[3] += int(okA and not okB)
        a[4] += int(okB and not okA)
        a[5] += rb["total_h_08_16"] - ra["total_h_08_16"]
    out = [[k, *v[:5], round(v[5] / v[0], 2)] for k, v in sorted(agg.items())]
    tot = [sum(r[i] for r in out) for i in range(1, 6)]
    out.append(["**전체**", *tot, round(
        sum(r["total_h_08_16"] for r in resB) / len(resB)
        - sum(r["total_h_08_16"] for r in resA) / len(resA), 2)])
    return out


def export_scenario(outdir: Path, tag: str, ctx, res, resA) -> None:
    from pyproj import CRS, Transformer
    to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                  always_xy=True)
    feats = []
    for p, r, ra in zip(ctx["points"], res, resA):
        now, base = pass_a(r), pass_a(ra)
        chg = ("유지 충족" if base and now else "신규 불충족" if base and not now
               else "신규 충족" if now else "유지 불충족")
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point",
                                   "coordinates": [round(p.x, 3), round(p.y, 3)]},
                      "properties": {
                          "pid": p.pid, "school": p.school, "kind": p.kind,
                          "dong": p.dong, "floor": p.floor,
                          "z_m": round(p.z, 2),
                          "a_pass": bool(base), "plan_pass": bool(now),
                          "total_h": r["total_h_08_16"],
                          "cont_h": r["cont_h_08_16"],
                          "change_cd": CHANGE_CODE[chg], "변화": chg}})
    write_geojson(outdir / f"{tag}_수광점_epsg5186.geojson", feats)
    write_geojson(outdir / f"{tag}_수광점_wgs84.geojson", feats, to_wgs)
    (outdir / f"{tag}_수광점_epsg5186.qml").write_text(
        qml_style("change_cd"), encoding="utf-8")


def write_massing(outdir: Path, ctx, daegyo, sibeom, hwarang_by_tag) -> None:
    items = []
    for name, prisms in [("대교", daegyo), ("시범", sibeom)]:
        for i, pr in enumerate(prisms):
            items.append((f"{name}{i}", pr.footprint,
                          {"group": name, "height_m": round(pr.top_m, 1)}))
    for tag, prisms in hwarang_by_tag.items():
        for i, pr in enumerate(prisms):
            items.append((f"{tag}{i}", pr.footprint,
                          {"group": tag, "height_m": round(pr.top_m, 1)}))
    write_geojson(outdir / "매싱_전체_epsg5186.geojson",
                  polygon_features(items))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    ctx = aa_setup(args)
    print(f"수광점 {len(ctx['receptors'])}개")

    hwarang = {
        "A_화랑현재": ctx["hwarang"],
        "B_화랑고층안": load_prisms_from_geojson(args.hwarang_highrise, "화랑고층"),
        "C_화랑20층이하안": load_prisms_from_geojson(args.hwarang_lowrise, "화랑20층"),
    }
    for tag, prisms in hwarang.items():
        h = max(p.top_m for p in prisms)
        print(f"  {tag}: {len(prisms)}개 매스 · 최고 {h:.1f}m")

    res: dict[str, list[dict[str, Any]]] = {}
    for tag, hw_prisms in hwarang.items():
        full = [*ctx["daegyo"], *ctx["sibeom"], *hw_prisms]
        res[tag] = H.evaluate(ctx["receptors"], full, ctx["times"],
                              ctx["masks"], args.step_min)
        n_ok = sum(1 for r in res[tag] if pass_a(r))
        print(f"  {tag}: 충족 {n_ok} / 불충족 {len(res[tag]) - n_ok}")

    for tag in ("B_화랑고층안", "C_화랑20층이하안"):
        export_scenario(args.outdir, tag, ctx, res[tag], res["A_화랑현재"])
    write_massing(args.outdir, ctx, ctx["daegyo"], ctx["sibeom"], hwarang)

    write_report(args.outdir / "report.md", ctx, res, hwarang)
    print(f"\n→ {args.outdir}")
    return 0


def write_report(path: Path, ctx, res, hwarang) -> None:
    n = len(ctx["receptors"])
    counts = {t: sum(1 for r in res[t] if pass_a(r)) for t in res}

    L = ["# 대교·시범 신축 + 화랑 3안 — 학교 일조 비교 (2026-09-20)", "",
         "대교·시범은 두 비교안 모두 **신축안**으로 고정하고, 화랑만 세",
         "가지로 바꿔 가며 학교 수광점(기준A: 동지 08~16시, 연속 2h 이상",
         "**또는** 총 4h 이상) 충족/불충족을 비교한다.", "",
         "| 안 | 화랑 구성 | 최고 높이 | 신규 불충족 대조군 |",
         "|---|---|---:|---|",
         f"| **A** | 화랑 현재상태(기존 10층 3개동) | "
         f"{max(p.top_m for p in hwarang['A_화랑현재']):.1f}m | (기준선) |",
         f"| **B** | 화랑 신축(고층) — 41층+저층부 7층 | "
         f"{max(p.top_m for p in hwarang['B_화랑고층안']):.1f}m | A 대비 |",
         f"| **C** | 화랑 신축(20층 이하) — 20층 1개동 | "
         f"{max(p.top_m for p in hwarang['C_화랑20층이하안']):.1f}m | A 대비 |",
         "", "## 전체 결과", "",
         "| 안 | 수광점 | 충족 | 불충족 | 충족률 |",
         "|---|---:|---:|---:|---:|"]
    for t, label in (("A_화랑현재", "A. 화랑 현재상태"),
                     ("B_화랑고층안", "B. 화랑 고층안"),
                     ("C_화랑20층이하안", "C. 화랑 20층이하안")):
        ok = counts[t]
        L.append(f"| {label} | {n:,} | {ok:,} | {n-ok:,} | {ok/n*100:.1f}% |")

    for tB, label in (("B_화랑고층안", "B(고층안)"),
                      ("C_화랑20층이하안", "C(20층이하안)")):
        tbl = school_table(ctx, res["A_화랑현재"], res[tB], tB)
        L += ["", f"## A → {label} — 학교·동별", "",
              "| 학교 / 구분 | 수광점 | A 충족 | {0} 충족 | 신규 불충족 | "
              "신규 충족 | 평균 일조 변화 |".format(label),
              "|---|---:|---:|---:|---:|---:|---:|"]
        for row in tbl:
            L.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | "
                     f"{row[4]} | {row[5]} | {row[6]:+.2f}h |")

    tblBC = school_table(ctx, res["B_화랑고층안"], res["C_화랑20층이하안"],
                         "C_화랑20층이하안")
    L += ["", "## B(고층안) → C(20층이하안) — 직접 비교", "",
          "두 화랑 신축안을 서로 비교한다(대교·시범은 동일). 양수는",
          "20층이하안이 고층안보다 나은 점이다.", "",
          "| 학교 / 구분 | 수광점 | B 충족 | C 충족 | B→C 신규 불충족 | "
          "B→C 신규 충족 | 평균 일조 변화 |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for row in tblBC:
        L.append(f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | "
                 f"{row[4]} | {row[5]} | {row[6]:+.2f}h |")

    okB, okC = counts["B_화랑고층안"], counts["C_화랑20층이하안"]
    L += ["", "## 요약", "",
          f"- A(화랑 현재상태) 충족 {counts['A_화랑현재']:,} / 불충족 "
          f"{n-counts['A_화랑현재']:,}",
          f"- B(화랑 고층안) 충족 {okB:,} / 불충족 {n-okB:,} "
          f"(A 대비 {okB-counts['A_화랑현재']:+,})",
          f"- C(화랑 20층이하안) 충족 {okC:,} / 불충족 {n-okC:,} "
          f"(A 대비 {okC-counts['A_화랑현재']:+,})",
          f"- **B와 C 중 학교 일조가 더 나은 쪽: "
          f"{'C(20층이하안)' if okC > okB else 'B(고층안)' if okB > okC else '동률'}"
          f"** (충족 {abs(okC-okB)}개 차이)", "",
          "## 산출물", "", "| 파일 | 내용 |", "|---|---|",
          "| `B_화랑고층안_수광점_*.geojson` | A 대비 B 전후 판정 |",
          "| `C_화랑20층이하안_수광점_*.geojson` | A 대비 C 전후 판정 |",
          "| `매싱_전체_epsg5186.geojson` | 대교·시범·화랑(A/B/C) 전체 매싱 |",
          ""]
    path.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
