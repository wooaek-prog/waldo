#!/usr/bin/env python3
"""학교 교사동 수광점 도면 – 정면도(입면)와 평면 배치도.

정면도: 파사드를 펼쳐 층별 창대(sill)~창상단(head) 띠를 그리고, 그 위에
        실제 수광점 위치를 점으로 찍는다. 사용자가 제공한 정면도와 같은
        방향으로 펼쳐 대조할 수 있게 한다.
평면도: 어느 외벽이 수광 대상 파사드로 잡혔는지(남측만) 표시한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import (
    load_named_buildings, school_label, school_parcel_rows,
)


def slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", text).strip("_")


def elevation_svg(path: Path, row: dict[str, Any], spec: dict[str, Any],
                  facade: dict[str, Any], receptors: Sequence[H.Receptor],
                  school: str, title: str, mode_txt: str = "") -> None:
    """파사드 전개 입면 + 수광점."""
    frame = SF.facade_frame(row, facade)
    total = SF.frame_total(frame)
    levels = SF.floor_levels(spec)
    sill, head = spec["window_sill_m"], spec["window_head_m"]
    nf = len(levels)
    top = max(SF.building_top(row, spec), levels[-1] + head + 0.4)
    az_txt = (f'{facade["azimuth_range"][0]:.0f}~{facade["azimuth_range"][1]:.0f}°'
              if facade.get("azimuth_range") else f'{facade["azimuth"]:.1f}°')
    mode_txt = mode_txt or SF.mode_note(spec, facade, receptors)

    px_w = 1500.0
    pad_l, pad_r, pad_t, pad_b = 92.0, 40.0, 96.0, 96.0
    scale = (px_w - pad_l - pad_r) / total
    px_h = top * scale + pad_t + pad_b

    def X(d: float) -> float:
        return pad_l + d * scale

    def Y(z: float) -> float:
        return pad_t + (top - z) * scale

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h:.0f}" viewBox="0 0 {px_w:.0f} {px_h:.0f}">',
           '<rect width="100%" height="100%" fill="#fcfcfa"/>',
           f'<text x="18" y="32" font-family="sans-serif" font-size="19" '
           f'font-weight="bold" fill="#111">{school} {title} – '
           f'파사드 전개 입면과 수광점</text>',
           f'<text x="18" y="56" font-family="sans-serif" font-size="12.5" '
           f'fill="#555">파사드 길이 {total:.1f}m · 높이 {top:.2f}m · '
           f'창 {sill:g}~{head:g}m(중앙 {(sill+head)/2:g}m) · 파사드 방위 '
           f'{az_txt} · {mode_txt} · 수광점 {len(receptors)}개</text>',
           f'<text x="18" y="76" font-family="sans-serif" font-size="12.5" '
           f'fill="#555">붉은 점 = 일조 계산 수광점(창 중앙 높이). '
           f'회색 빗금 = 교실창 없음으로 제외한 구간</text>']

    # 건물 외곽
    svg.append(f'<rect x="{X(0):.1f}" y="{Y(top):.1f}" '
               f'width="{total*scale:.1f}" height="{top*scale:.1f}" '
               f'fill="#ffffff" stroke="#333" stroke-width="1.6"/>')
    skip = set(spec.get("skip_floors", []))
    active = {r.floor for r in receptors}
    # 층선 + 층 라벨
    for f, z0 in enumerate(levels, start=1):
        svg.append(f'<line x1="{X(0):.1f}" y1="{Y(z0):.1f}" '
                   f'x2="{X(total):.1f}" y2="{Y(z0):.1f}" stroke="#bbb" '
                   f'stroke-width="0.9" stroke-dasharray="5 4"/>')
        if f not in active:
            z1 = levels[f] if f < nf else z0 + head + 0.4
            why = ("교실 없음(필로티 등)으로 제외" if f in skip
                   else "이 면에는 수광점 없음")
            svg.append(f'<rect x="{X(0):.1f}" y="{Y(z1):.1f}" '
                       f'width="{total*scale:.1f}" height="{(z1-z0)*scale:.1f}" '
                       f'fill="#8a8a82" fill-opacity="0.18"/>')
            svg.append(f'<text x="{X(total/2):.1f}" '
                       f'y="{Y(z0+(z1-z0)/2)+4:.1f}" font-family="sans-serif" '
                       f'font-size="12" text-anchor="middle" fill="#777">'
                       f'{f}층 – {why}</text>')
        svg.append(f'<text x="{X(0)-10:.1f}" y="{Y(z0+(sill+head)/2)+4:.1f}" '
                   f'font-family="sans-serif" font-size="11.5" text-anchor="end" '
                   f'fill="#111">{f}층</text>')
        svg.append(f'<text x="{X(0)-10:.1f}" y="{Y(z0)+4:.1f}" '
                   f'font-family="sans-serif" font-size="9.5" text-anchor="end" '
                   f'fill="#999">{z0:.1f}m</text>')
    # 창 – 수광점 1개당 창 1개소로 그린다(분석지점도와 같은 표기)
    win_w = float(spec.get("window_width_m", 2.4))
    for r in receptors:
        d = SF.frame_distance(frame, r.x, r.y)
        z0 = levels[r.floor - 1]
        svg.append(f'<rect x="{X(max(0.0, d-win_w/2)):.1f}" y="{Y(z0+head):.1f}" '
                   f'width="{win_w*scale:.1f}" height="{(head-sill)*scale:.1f}" '
                   f'fill="#cfe0f0" fill-opacity="0.85" stroke="#5d7fa3" '
                   f'stroke-width="0.8"/>')
    # 제외 구간 – 지정한 층만 덮는다
    for a, bnd, floors in SF.excluded_spans(facade):
        rng = range(1, nf + 1) if floors is None else sorted(floors)
        for f in rng:
            if f > nf:
                continue
            z0 = levels[f - 1]
            z1 = levels[f] if f < nf else z0 + (head + 0.4)
            svg.append(f'<rect x="{X(a):.1f}" y="{Y(z1):.1f}" '
                       f'width="{(bnd-a)*scale:.1f}" height="{(z1-z0)*scale:.1f}" '
                       f'fill="#8a8a82" fill-opacity="0.30" stroke="#8a8a82" '
                       f'stroke-width="0.8"/>')
        ztop = levels[min(max(rng), nf) - 1] + (head + 0.4)
        svg.append(f'<text x="{X((a+bnd)/2):.1f}" y="{Y(ztop)-4:.1f}" '
                   f'font-family="sans-serif" font-size="10" '
                   f'text-anchor="middle" fill="#555">'
                   f'{"전층" if floors is None else ",".join(str(f) for f in rng)+"층"}'
                   f' 제외</text>')

    # 수광점 – receptors_from_facade 와 동일한 전개 좌표계를 쓴다
    for r in receptors:
        d = SF.frame_distance(frame, r.x, r.y)
        svg.append(f'<circle cx="{X(d):.1f}" cy="{Y(r.z):.1f}" r="2.9" '
                   f'fill="#c1121f" stroke="#fff" stroke-width="0.7"/>')

    # 치수선
    yb = Y(0) + 26
    svg.append(f'<line x1="{X(0):.1f}" y1="{yb:.1f}" x2="{X(total):.1f}" '
               f'y2="{yb:.1f}" stroke="#111" stroke-width="1"/>')
    for d in range(0, int(total) + 1, 10):
        svg.append(f'<line x1="{X(d):.1f}" y1="{yb-4:.1f}" x2="{X(d):.1f}" '
                   f'y2="{yb+4:.1f}" stroke="#111" stroke-width="1"/>')
        svg.append(f'<text x="{X(d):.1f}" y="{yb+18:.1f}" '
                   f'font-family="sans-serif" font-size="10.5" '
                   f'text-anchor="middle" fill="#111">{d}</text>')
    svg.append(f'<text x="{X(total/2):.1f}" y="{yb+38:.1f}" '
               f'font-family="sans-serif" font-size="11.5" text-anchor="middle" '
               f'fill="#555">파사드 전개 거리 (m)</text>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def plan_svg(path: Path, pairs: Sequence[tuple[dict[str, Any], dict[str, Any]]],
             others: Sequence[dict[str, Any]],
             receptors: Sequence[H.Receptor], school: str) -> None:
    """평면 – 수광 대상 파사드(남측)와 수광점 위치."""
    geoms = [r["geom"] for r, _ in pairs] + [r["geom"] for r in others]
    minx, miny, maxx, maxy = unary_union(geoms).buffer(16).bounds
    # 폭·높이 양쪽에 맞춰 축척을 잡는다(한쪽만 쓰면 도면이 잘린다)
    head, foot, pad = 100.0, 30.0, 24.0
    max_w, max_h = 1180.0, 860.0
    scale = min((max_w - 2 * pad) / (maxx - minx),
                (max_h - head - foot) / (maxy - miny))
    px_w = (maxx - minx) * scale + 2 * pad
    px_h = (maxy - miny) * scale + head + foot

    def P(x, y):
        return (x - minx) * scale + pad, (maxy - y) * scale + head

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{px_w:.0f}" '
           f'height="{px_h:.0f}" viewBox="0 0 {px_w:.0f} {px_h:.0f}">',
           '<rect width="100%" height="100%" fill="#fcfcfa"/>',
           f'<text x="18" y="32" font-family="sans-serif" font-size="19" '
           f'font-weight="bold" fill="#111">{school} – 수광 대상 파사드(남측)와 '
           f'수광점 평면</text>',
           '<text x="18" y="56" font-family="sans-serif" font-size="12.5" '
           'fill="#555">굵은 청색 = 수광점을 두는 남측 외벽 / 회색 = 대상 외 '
           '외벽(북측·단부벽). 상단이 북.</text>',
           '<text x="18" y="76" font-family="sans-serif" font-size="12.5" '
           'fill="#555">붉은 점은 각 층 수광점이 평면상 겹쳐 보이는 위치입니다.</text>']

    def draw_footprint(row, fill):
        g = row["geom"]
        for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            pts = " ".join(f"{P(x,y)[0]:.1f},{P(x,y)[1]:.1f}"
                           for x, y in poly.exterior.coords)
            svg.append(f'<polygon points="{pts}" fill="{fill}" stroke="#9b9b93" '
                       f'stroke-width="1.4"/>')

    for row in others:
        draw_footprint(row, "#f2f1ec")
    for row, spec in pairs:
        draw_footprint(row, "#eceae4")
        for facade in SF.spec_facades(spec):
            for a, bnd in SF.facade_segments(row["geom"], facade):
                svg.append(f'<line x1="{P(*a)[0]:.1f}" y1="{P(*a)[1]:.1f}" '
                           f'x2="{P(*bnd)[0]:.1f}" y2="{P(*bnd)[1]:.1f}" '
                           f'stroke="#1d3557" stroke-width="5" '
                           f'stroke-linecap="round"/>')
        c = row["geom"].centroid
        svg.append(f'<text x="{P(c.x,c.y)[0]:.1f}" y="{P(c.x,c.y)[1]:.1f}" '
                   f'font-family="sans-serif" font-size="13" '
                   f'font-weight="bold" text-anchor="middle" fill="#111">'
                   f'{spec["id"]}</text>')
    seen = set()
    for r in receptors:
        k = (round(r.x, 1), round(r.y, 1))
        if k in seen:
            continue
        seen.add(k)
        x, y = P(r.x, r.y)
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.6" fill="#c1121f" '
                   f'stroke="#fff" stroke-width="0.6"/>')
    svg.append(f'<g transform="translate({px_w-64:.0f},104)">'
               '<line x1="0" y1="40" x2="0" y2="6" stroke="#111" stroke-width="2"/>'
               '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
               '<text x="0" y="56" font-family="sans-serif" font-size="12" '
               'text-anchor="middle" fill="#111">N</text></g>')
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="학교 수광점 도면")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--jibun", type=str, default="40-3")
    p.add_argument("--outdir", type=Path, default=Path("outputs/school_receptors"))
    p.add_argument("--school-radius", type=float, default=350.0)
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    buildings = load_named_buildings(args.buildings)
    daegyo = H.load_daegyo(args.daegyo)
    centre = unary_union([p.footprint for p in daegyo]).centroid
    schools = school_parcel_rows(buildings, centre, args.school_radius)
    rows = schools[args.jibun]
    school = school_label(args.jibun, rows).split(" ", 1)[1]

    spec_all = SF.load_spec()
    entry = spec_all.get(args.jibun)
    if not entry:
        raise SystemExit(f"{args.jibun} 정면도 정의가 없습니다.")
    rows = SF.augment_rows(args.jibun, rows, buildings, entry)

    all_rec: list[H.Receptor] = []
    feats: list[dict[str, Any]] = []
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    print(f"■ {school} 수광점 도면")
    for spec in entry["buildings"]:
        row = SF.find_row(rows, spec)
        if row is None:
            print(f"   {spec['id']}: 대응하는 건물 폴리곤을 찾지 못함 – 건너뜀")
            continue
        pairs.append((row, spec))
        # 같은 외벽면을 층별로 나눠 정의한 경우(모듈이 층마다 다른 동) 한 장으로 합친다
        groups: dict[str, dict[str, Any]] = {}
        for i, facade in enumerate(SF.spec_facades(spec)):
            fid = SF.facade_id(spec, facade, i)
            rec = SF.receptors_from_facade(row, spec, facade, fid)
            if not rec:
                print(f"   {fid}: 해당 방위의 외벽 없음 – 건너뜀")
                continue
            key = facade.get("group") or (
                f'{facade.get("azimuth", facade.get("azimuth_range"))}'
                f'/{facade.get("tol_deg", 30.0)}/{facade.get("bay_origin_m", 0.0)}')
            g = groups.setdefault(key, {"facade": facade, "id": fid, "rec": [],
                                        "excl": [], "modes": []})
            g["rec"] += rec
            g["excl"] += facade.get("exclude_spans", [])
            g["modes"].append(SF.mode_note(spec, facade, rec))
            if facade.get("group"):
                g["id"] = facade["group"]
        for g in groups.values():
            facade, fid, rec = g["facade"], g["id"], g["rec"]
            draw_facade = {**facade, "exclude_spans": g["excl"]}
            all_rec += rec
            out = args.outdir / f"{args.jibun}_{slug(fid)}_elevation.svg"
            elevation_svg(out, row, spec, draw_facade, rec, school, fid,
                          " / ".join(dict.fromkeys(g["modes"])))
            per = Counter(r.floor for r in rec)
            detail = " ".join(f"{f}층 {per[f]}" for f in sorted(per))
            print(f"   {fid}: 수광점 {len(rec)}개 ({detail}) → {out.name}")
            for r in rec:
                feats.append({"type": "Feature",
                              "geometry": {"type": "Point",
                                           "coordinates": [r.x, r.y]},
                              "properties": {"school": school, "building": spec["id"],
                                             "facade": fid, "floor": r.floor,
                                             "z_m": round(r.z, 2),
                                             "normal_az": round(r.normal_az, 1)}})

    claimed = {id(r) for r, _ in pairs}
    others = [r for r in rows if id(r) not in claimed]
    plan_svg(args.outdir / f"{args.jibun}_plan.svg", pairs, others, all_rec, school)
    (args.outdir / f"{args.jibun}_receptors.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::5186"}},
        "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")
    with (args.outdir / f"{args.jibun}_receptors.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["학교", "교사동", "파사드", "층", "X", "Y", "Z", "창면방위"])
        for f in feats:
            p = f["properties"]
            x, y = f["geometry"]["coordinates"]
            w.writerow([p["school"], p["building"], p["facade"], p["floor"],
                        round(x, 2), round(y, 2), p["z_m"], p["normal_az"]])
    print(f"   합계 수광점 {len(all_rec)}개 · 평면도 + GeoJSON/CSV 저장: {args.outdir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
