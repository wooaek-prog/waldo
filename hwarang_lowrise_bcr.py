#!/usr/bin/env python3
"""화랑 – 20층 이하에서 건폐율의 산술적 하한 (소규모재건축 전제).

일조 계산을 돌리지 않는다. **건폐율은 층수와 용적률이 정해지는 순간 거의
결정된다**는 걸 보이는 게 목적이다.

    연면적 = 대지 × 용적률          (고정)
    기준층 = 연면적 ÷ 층수          (한 덩어리로 지을 때)
    건축면적 = 기준층 + 둘레 여유    (발코니 1.5m − 건폐율 완화 1.0m = 0.5m)

그래서 건폐율 하한 = 용적률 ÷ 층수 + (둘레 여유분). 400% ÷ 20층 = 20.0% 가
바닥이고, 둘레 여유가 형상에 따라 0.9~1.5%p 얹힌다. 층수를 낮추면 하한이
그대로 올라간다(19층 21.05%, 18층 22.22%, …).

동을 둘로 쪼개도 합계 기준층은 같아 건폐율은 **낮아지지 않는다** — 둘레만
늘어 오히려 조금 올라간다. 저층부(포디움)도 마찬가지로 건폐율을 키운다.
남는 자유도는 형상·방위·위치뿐이고, 그건 일조로 고르는 게 맞다
(→ `hwarang_design_2026.py --floors-max 20`).

    python3 hwarang_lowrise_bcr.py
    python3 hwarang_lowrise_bcr.py --floors-lo 15 --floors-hi 22
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import shape

from hwarang_redesign import BALCONY_M, COVERAGE_INSET_M, plate_polygon

RESI_FLOOR_H = 2.95
ROOFTOP_M = 4.0
# 대지 폴리곤은 종전 산출물에서 읽는다 — gpkg 전체를 다시 훑지 않기 위해서다.
SITE_GEOJSON = Path("outputs/hwarang_nopodium_2026/최적안_매싱_epsg5186.geojson")
ASPECTS = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0)


def load_site(path: Path):
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        if f["properties"].get("kind") == "대지":
            return shape(f["geometry"])
    raise SystemExit(f"{path} 에 대지 폴리곤이 없습니다.")


def fits(envelope, plate: float, aspect: float, az_step: float = 5.0,
         pos_step: float = 3.0) -> tuple[bool, float | None]:
    """외형선(발코니 끝)이 이격선 안에 들어가는 방위가 있는지.

    들어가면 (True, 그 방위) — 여러 방위가 되면 가장 먼저 찾은 것.
    """
    minx, miny, maxx, maxy = envelope.bounds
    az = 0.0
    while az < 180.0:
        y = miny
        while y <= maxy:
            x = minx
            while x <= maxx:
                if envelope.contains(plate_polygon(plate, aspect, az, x, y,
                                                   BALCONY_M)):
                    return True, az
                x += pos_step
            y += pos_step
        az += az_step
    return False, None


def rows(site, gfa: float, site_area: float, setback: float,
         floors_lo: int, floors_hi: int) -> list[dict[str, Any]]:
    envelope = site.buffer(-setback)
    out = []
    for f in range(floors_hi, floors_lo - 1, -1):
        plate = gfa / f
        floor_lo = gfa / f / site_area * 100          # 둘레 여유 0 일 때의 하한
        best = None
        for asp in ASPECTS:
            short = math.sqrt(plate / asp)
            lng = plate / short
            # 건축면적선 = 연면적선 + 0.5m (발코니 1.5 − 건폐율 완화 1.0)
            e = BALCONY_M - COVERAGE_INSET_M
            cover = (lng + 2 * e) * (short + 2 * e)
            ok, az = fits(envelope, plate, asp)
            r = {"floors": f, "aspect": asp, "plate_m2": plate,
                 "long_m": lng, "short_m": short, "cover_m2": cover,
                 "bcr_pct": cover / site_area * 100,
                 "floor_lo_pct": floor_lo, "fits": ok, "azimuth": az,
                 "height_m": f * RESI_FLOOR_H + ROOFTOP_M}
            out.append(r)
            if ok and (best is None or r["bcr_pct"] < best["bcr_pct"]):
                best = r
    return out


def main(argv: Sequence[str] | None = None) -> int:
    global RESI_FLOOR_H
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--site", type=Path, default=SITE_GEOJSON)
    p.add_argument("--site-area", type=float, default=9395.0)
    p.add_argument("--far", type=float, default=400.0)
    p.add_argument("--setback", type=float, default=3.0)
    p.add_argument("--daylight-multiple", type=float, default=4.0)
    p.add_argument("--floors-lo", type=int, default=15)
    p.add_argument("--floors-hi", type=int, default=20)
    p.add_argument("--floor-h", type=float, default=RESI_FLOOR_H,
                   help="주거 층고 m")
    p.add_argument("--blocks", type=int, nargs="*", default=[1, 2, 3, 4, 5, 6],
                   help="분동 수별 건폐율·실면적 비교표에 넣을 동 수")
    p.add_argument("--block-aspect", type=float, default=4.0,
                   help="분동 비교표에서 각 동에 쓸 세장비")
    p.add_argument("--out", type=Path,
                   default=Path("outputs/hwarang_20f_2026/건폐율하한.md"))
    a = p.parse_args(argv)

    RESI_FLOOR_H = a.floor_h
    site = load_site(a.site)
    gfa = a.site_area * a.far / 100.0
    rs = rows(site, gfa, a.site_area, a.setback, a.floors_lo, a.floors_hi)

    L = ["# 20층 이하 · 용적률 400% – 건폐율의 산술적 하한", "",
         f"대지 {a.site_area:,.0f}㎡ · 연면적 {gfa:,.0f}㎡ · "
         f"대지경계 이격 {a.setback:g}m · 건축면적선 = 연면적선 +"
         f"{BALCONY_M - COVERAGE_INSET_M:g}m", "",
         "## 층수별 하한", "",
         "| 층수 | 높이 | 기준층(한 덩어리) | 건폐율 하한(둘레 0) | "
         "실현 가능한 최소 건폐율 | 그때의 형상 |",
         "|---:|---:|---:|---:|---:|---|"]
    print(f"대지 {a.site_area:,.0f}㎡ · 연면적 {gfa:,.0f}㎡")
    print(f"{'층':>4}{'높이m':>8}{'기준층㎡':>11}{'하한%':>8}"
          f"{'최소건폐율%':>12}  형상")
    for f in range(a.floors_hi, a.floors_lo - 1, -1):
        same = [r for r in rs if r["floors"] == f]
        ok = [r for r in same if r["fits"]]
        b = min(ok, key=lambda r: r["bcr_pct"]) if ok else None
        base = same[0]
        shape_s = (f"{b['aspect']:g}:1 {b['long_m']:.1f}×{b['short_m']:.1f}m "
                   f"(방위 {b['azimuth']:.0f}°)" if b else "대지 안에 안 들어감")
        print(f"{f:>4}{base['height_m']:>8.1f}{base['plate_m2']:>11,.0f}"
              f"{base['floor_lo_pct']:>8.2f}"
              + (f"{b['bcr_pct']:>12.2f}" if b else f"{'—':>12}")
              + f"  {shape_s}")
        L.append(f"| {f}층 | {base['height_m']:,.1f}m | "
                 f"{base['plate_m2']:,.0f}㎡ | {base['floor_lo_pct']:.2f}% | "
                 + (f"**{b['bcr_pct']:.2f}%**" if b else "—")
                 + f" | {shape_s} |")

    L += ["", f"## {a.floors_hi}층 형상별 건축면적", "",
          "| 세장비 | 기준층 치수 | 건축면적 | 건폐율 | 대지 안 배치 |",
          "|---:|---|---:|---:|---|"]
    print(f"\n{a.floors_hi}층 형상별")
    print(f"{'세장비':>7}{'장변m':>9}{'단변m':>8}{'건축면적㎡':>12}"
          f"{'건폐율%':>9}  배치")
    for r in [r for r in rs if r["floors"] == a.floors_hi]:
        s = (f"가능(방위 {r['azimuth']:.0f}°)" if r["fits"]
             else f"불가 — 이격선({a.setback:g}m) 밖")
        print(f"{r['aspect']:>6g}:1{r['long_m']:>9.1f}{r['short_m']:>8.1f}"
              f"{r['cover_m2']:>12,.0f}{r['bcr_pct']:>9.2f}  {s}")
        L.append(f"| {r['aspect']:g}:1 | {r['long_m']:.1f} × "
                 f"{r['short_m']:.1f}m | {r['cover_m2']:,.0f}㎡ | "
                 f"{r['bcr_pct']:.2f}% | {s} |")

    # 분동 수별 — 건폐율은 조금 오르고 서비스면적(발코니)은 크게 는다.
    # 발코니는 둘레에 비례하고, 둘레는 동 수의 제곱근에 비례해 커진다.
    L += ["", f"## {a.floors_hi}층 분동 수별 – 건폐율 vs 실면적 "
          f"(각 동 {a.block_aspect:g}:1)", "",
          "| 동 수 | 동당 기준층 | 동당 치수 | 건축면적 | 건폐율 | "
          "서비스면적 | 실면적 | 단일 대비 |",
          "|---:|---:|---|---:|---:|---:|---:|---:|"]
    print(f"\n{a.floors_hi}층 분동 수별 (세장비 {a.block_aspect:g}:1)")
    print(f"{'동수':>4}{'동당기준층㎡':>13}{'건축면적㎡':>12}{'건폐율%':>9}"
          f"{'서비스㎡':>10}{'실면적㎡':>11}{'단일대비':>10}")
    e = BALCONY_M - COVERAGE_INSET_M
    base_real = None
    for n in a.blocks:
        p_ = gfa / (n * a.floors_hi)
        short = math.sqrt(p_ / a.block_aspect)
        lng = p_ / short
        cover = (lng + 2 * e) * (short + 2 * e) * n
        serv = ((lng + 2 * BALCONY_M) * (short + 2 * BALCONY_M) - p_) \
            * a.floors_hi * n
        real = gfa + serv
        if base_real is None:
            base_real = real
        print(f"{n:>4}{p_:>13,.0f}{cover:>12,.0f}"
              f"{cover / a.site_area * 100:>9.2f}{serv:>10,.0f}{real:>11,.0f}"
              f"{real - base_real:>+10,.0f}")
        L.append(f"| {n}개동 | {p_:,.0f}㎡ | {lng:.1f} × {short:.1f}m | "
                 f"{cover:,.0f}㎡ | {cover / a.site_area * 100:.2f}% | "
                 f"{serv:,.0f}㎡ | {real:,.0f}㎡ | {real - base_real:+,.0f}㎡ |")

    need = (a.floors_hi * RESI_FLOOR_H + ROOFTOP_M) / a.daylight_multiple
    L += ["", "## 채광이격", "",
          f"{a.floors_hi}층 = {a.floors_hi * RESI_FLOOR_H + ROOFTOP_M:,.1f}m. "
          f"준주거 {a.daylight_multiple:g}배 규정이면 채광창면에서 대지경계까지 "
          f"**{need:,.2f}m** 가 필요하다.",
          "대지 단변이 76.5m 라 외형선 단변이 "
          f"{76.5 - 2 * need:,.1f}m 이하면 양쪽 다 확보된다 — "
          "20층대에서는 여유가 있다(고층안이 못 지키던 것과 다른 점).", ""]
    print(f"\n채광이격 {a.daylight_multiple:g}배 필요거리 {need:.2f}m "
          f"(높이 {a.floors_hi * RESI_FLOOR_H + ROOFTOP_M:.1f}m)")
    print(f"→ 외형선 단변 {76.5 - 2 * need:.1f}m 이하면 대지 단변(76.5m) 안에서 "
          "양쪽 확보")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(L), encoding="utf-8")
    print(f"\n→ {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
