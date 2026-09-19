#!/usr/bin/env python3
"""대교·시범 재건축 + 화랑 현상태 — 불충족 수광점의 원인 아파트 세분화.

시나리오는 하나다: **대교 신축안 + 시범 신축안 + 화랑 현재 상태(기존 10층
3개동)**. 전후 비교가 아니라 이 구성에서 지금 불충족인 점이 몇 개고, 각각을
누가(대교/시범/화랑/기타 기존건물) 가리는지를 묻는다.

'누가 가리는가'는 반사실(counterfactual)로 구한다 — 그 점이 불충족인 채로
1) 대교만 빼고, 2) 시범만 빼고, 3) 화랑만 빼고 다시 평가해, 어느 쪽을 뺐을 때
일조시간이 가장 많이 회복되는지를 본다. 셋 다 빼도(= '기타 기존건물' 절만
남겨도) 회복이 없으면 세 재건축 대상과 무관한 결함(자기 그늘·기타 기존건물)
으로 분류한다.

    python3 apartment_attribution.py --buildings <AL_D010.gpkg>
    # → outputs/attribution_2026/  report.md · 수광점_불충족_원인_*.geojson · 배치도
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import urllib.parse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import shape
from shapely.ops import unary_union

import hwarang_massing_study as H
import school_facade_receptors as SF
from daegyo_school_sunlight import (
    DAEGYO_JIBUN, HWARANG_JIBUN, apartment_prisms, load_named_buildings,
    school_parcel_rows,
)
from school_receptor_compliance import (
    build_context, build_points, group_rows, pass_a, polygon_features,
    write_geojson,
)

SIBEOM_JIBUN = "50"
CHROME = ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/opt/pw-browsers/chromium/chrome-linux/chrome")

GROUP_COLOR = {
    "대교": "#e63946", "시범": "#f4a261", "화랑(현상태)": "#8338ec",
    "복합(2개 이상)": "#6c757d", "기타/자체형상": "#adb5bd",
}


def load_prisms_from_geojson(path: Path, label: str,
                             layer_filter: str | None = None) -> list[H.Prism]:
    """대교·시범 산출 GeoJSON(외형선+height_m)에서 프리즘을 읽는다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for f in data["features"]:
        p = f["properties"]
        if layer_filter is not None and p.get("layer") != layer_filter:
            continue
        h = p.get("height_m")
        if not h:
            continue
        g = shape(f["geometry"])
        for poly in (g.geoms if g.geom_type == "MultiPolygon" else [g]):
            out.append(H.Prism(poly, float(h), f"{label} {p.get('dong', '')}"))
    return out


def setup(args) -> dict[str, Any]:
    from pyproj import CRS, Transformer

    month, day = (int(v) for v in args.date.split("-"))
    buildings = load_named_buildings(args.buildings)

    daegyo_new = load_prisms_from_geojson(args.daegyo, "대교")
    sibeom_new = load_prisms_from_geojson(args.sibeom, "시범", layer_filter="신축동")
    if not daegyo_new:
        raise SystemExit(f"대교 프리즘을 못 읽었다: {args.daegyo}")
    if not sibeom_new:
        raise SystemExit(f"시범 프리즘을 못 읽었다: {args.sibeom}")
    hwarang_now = apartment_prisms(buildings, HWARANG_JIBUN, "화랑 기존")

    centre = unary_union([p.footprint for p in daegyo_new]).centroid
    lon, lat = Transformer.from_crs(
        CRS.from_epsg(5186), CRS.from_epsg(4326), always_xy=True
    ).transform(centre.x, centre.y)
    times = H.sun_track(month, day, lat, lon, step_min=args.step_min)

    spec_all = SF.load_spec()
    schools = school_parcel_rows(buildings, centre, args.school_radius)
    all_b = unary_union([r["geom"] for r in buildings])
    points, receptors, grounds, dropped = build_points(
        schools, buildings, spec_all, all_b, args.apron, {}, args.pg_step)

    # 시범 대지(지번 50)의 기존 건물은 '기타 기존건물' 차폐물에서 뺀다 —
    # 신축안(sibeom_new)이 그 자리를 대신하므로 중복 계산을 막는다.
    non_sibeom = [r for r in buildings if r["jibun"] != SIBEOM_JIBUN]
    context, n_recovered = build_context(non_sibeom, centre, args.context_radius,
                                         spec_all)
    masks = H.build_context_masks(context, receptors, times)

    return {
        "buildings": buildings, "points": points, "receptors": receptors,
        "grounds": grounds, "n_dropped": len(dropped), "times": times,
        "context": context, "masks": masks, "n_recovered": n_recovered,
        "daegyo": daegyo_new, "sibeom": sibeom_new, "hwarang": hwarang_now,
        "centre": centre,
    }


def evaluate_all(ctx: dict[str, Any], step_min: int
                 ) -> dict[str, list[dict[str, Any]]]:
    """전체 + 그룹별 1개 제외 시나리오를 전부 평가한다."""
    groups = {"대교": ctx["daegyo"], "시범": ctx["sibeom"],
              "화랑(현상태)": ctx["hwarang"]}
    full = [*ctx["daegyo"], *ctx["sibeom"], *ctx["hwarang"]]
    out = {"전체": H.evaluate(ctx["receptors"], full, ctx["times"],
                             ctx["masks"], step_min)}
    for name in groups:
        without = [p for g, ps in groups.items() if g != name for p in ps]
        out[f"제외_{name}"] = H.evaluate(ctx["receptors"], without, ctx["times"],
                                        ctx["masks"], step_min)
    return out


def attribute(ctx: dict[str, Any], res: dict[str, list[dict[str, Any]]],
             ) -> list[dict[str, Any]]:
    """실패한 수광점마다 그룹별 회복 시간을 재 원인을 정한다."""
    points = ctx["points"]
    full = res["전체"]
    names = ["대교", "시범", "화랑(현상태)"]
    rows = []
    for i, (p, r) in enumerate(zip(points, full)):
        ok = pass_a(r)
        recov = {n: round(res[f"제외_{n}"][i]["total_h_08_16"]
                          - r["total_h_08_16"], 2) for n in names}
        flips = {n: pass_a(res[f"제외_{n}"][i]) and not ok for n in names}
        # 주원인 = 뺐을 때 회복 시간이 가장 큰 그룹(0.05h 미만은 무시)
        cand = {n: v for n, v in recov.items() if v >= 0.05}
        if not cand:
            cause, group_tag = "기타/자체형상", []
        else:
            top = max(cand.values())
            contributors = [n for n, v in cand.items() if v >= top * 0.5]
            group_tag = contributors
            cause = contributors[0] if len(contributors) == 1 else "복합(2개 이상)"
        rows.append({
            "point": p, "ok": ok, "total_h": r["total_h_08_16"],
            "cont_h": r["cont_h_08_16"], "recov": recov, "flips": flips,
            "cause": cause, "contributors": group_tag,
        })
    return rows


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--sibeom", type=Path,
                   default=Path("outputs/sibeom/sibeom_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path, default=Path("outputs/attribution_2026"))
    p.add_argument("--date", type=str, default="12-22")
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--context-radius", type=float, default=500.0)
    p.add_argument("--step-min", type=int, default=10)
    p.add_argument("--apron", type=float, default=60.0)
    p.add_argument("--pg-step", type=float, default=8.0)
    return p.parse_args(argv)


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
def write_outputs(args, ctx: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    from pyproj import CRS, Transformer
    outdir = args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    to_wgs = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                  always_xy=True)

    all_feats, fail_feats = [], []
    for row in rows:
        p = row["point"]
        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(p.x, 3),
                                                          round(p.y, 3)]},
            "properties": {
                "pid": p.pid, "school": p.school, "jibun": p.jibun,
                "kind": p.kind, "dong": p.dong, "floor": p.floor,
                "source": p.source, "total_h": row["total_h"],
                "cont_h": row["cont_h"], "충족": "충족" if row["ok"] else "불충족",
                "원인": row["cause"],
                "회복_대교": row["recov"]["대교"], "회복_시범": row["recov"]["시범"],
                "회복_화랑": row["recov"]["화랑(현상태)"],
            }}
        all_feats.append(feat)
        if not row["ok"]:
            fail_feats.append(feat)

    write_geojson(outdir / "수광점_전체_epsg5186.geojson", all_feats)
    write_geojson(outdir / "수광점_전체_wgs84.geojson", all_feats, to_wgs)
    write_geojson(outdir / "수광점_불충족_원인_epsg5186.geojson", fail_feats)
    write_geojson(outdir / "수광점_불충족_원인_wgs84.geojson", fail_feats, to_wgs)
    (outdir / "수광점_불충족_원인_epsg5186.qml").write_text(
        qml_cause_style(), encoding="utf-8")
    (outdir / "수광점_불충족_원인_wgs84.qml").write_text(
        qml_cause_style(), encoding="utf-8")

    # 매싱 레이어(참고용)
    items = []
    for name, prisms in (("대교", ctx["daegyo"]), ("시범", ctx["sibeom"]),
                         ("화랑(현상태)", ctx["hwarang"])):
        for i, pr in enumerate(prisms):
            items.append((f"{name}{i}", pr.footprint,
                          {"group": name, "height_m": round(pr.top_m, 1)}))
    massing = polygon_features(items)
    write_geojson(outdir / "매싱_epsg5186.geojson", massing)
    write_geojson(outdir / "매싱_wgs84.geojson", massing, to_wgs)


def qml_cause_style() -> str:
    cats, syms = [], []
    for i, (name, color) in enumerate(GROUP_COLOR.items()):
        rgb = tuple(int(color.lstrip("#")[j:j+2], 16) for j in (0, 2, 4))
        cats.append(f'      <category value="{name}" symbol="{i}" '
                    f'label="{name}" render="true"/>')
        syms.append(
            f'      <symbol type="marker" name="{i}" alpha="1">\n'
            f'        <layer class="SimpleMarker">\n'
            f'          <Option type="Map">\n'
            f'            <Option name="name" type="QString" value="circle"/>\n'
            f'            <Option name="color" type="QString" '
            f'value="{rgb[0]},{rgb[1]},{rgb[2]},255"/>\n'
            f'            <Option name="outline_color" type="QString" '
            f'value="30,30,30,255"/>\n'
            f'            <Option name="outline_width" type="QString" value="0.2"/>\n'
            f'            <Option name="size" type="QString" value="3.2"/>\n'
            f'            <Option name="size_unit" type="QString" value="MM"/>\n'
            f'          </Option>\n'
            f'        </layer>\n'
            f'      </symbol>')
    return ("<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
            '<qgis version="3.28.0" styleCategories="Symbology">\n'
            '  <renderer-v2 type="categorizedSymbol" attr="원인" '
            'forceraster="0" symbollevels="0" enableorderby="0">\n'
            "    <categories>\n" + "\n".join(cats) + "\n    </categories>\n"
            "    <symbols>\n" + "\n".join(syms) + "\n    </symbols>\n"
            "  </renderer-v2>\n</qgis>\n")


def plan_svg(path: Path, ctx: dict[str, Any], rows: list[dict[str, Any]]
            ) -> tuple[int, int]:
    geoms = ([p.footprint for p in ctx["context"]]
             + [p.footprint for p in ctx["daegyo"]]
             + [p.footprint for p in ctx["sibeom"]]
             + [p.footprint for p in ctx["hwarang"]])
    minx, miny, maxx, maxy = unary_union(geoms).buffer(20).bounds
    head, foot, pad = 128.0, 34.0, 22.0
    scale = min((1480.0 - 2 * pad) / (maxx - minx),
                (1040.0 - head - foot) / (maxy - miny))
    W = (maxx - minx) * scale + 2 * pad
    Hh = (maxy - miny) * scale + head + foot

    def P(x, y):
        return (x - minx) * scale + pad, (maxy - y) * scale + head

    def poly_str(poly):
        return " ".join(f"{P(x,y)[0]:.1f},{P(x,y)[1]:.1f}"
                        for x, y in poly.exterior.coords)

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" '
         f'height="{Hh:.0f}" viewBox="0 0 {W:.0f} {Hh:.0f}">',
         '<rect width="100%" height="100%" fill="#fcfcfa"/>',
         '<text x="18" y="32" font-family="sans-serif" font-size="19" '
         'font-weight="bold" fill="#111">대교·시범 신축 + 화랑 현상태 — '
         '수광점 불충족 원인 아파트</text>',
         '<text x="18" y="56" font-family="sans-serif" font-size="12.5" '
         'fill="#555">회색 = 충족 · 점 색 = 불충족 원인(가장 회복시간이 큰 '
         '재건축 대상). 크기가 클수록 불충족.</text>']
    for p in ctx["context"]:
        for q in (p.footprint.geoms if p.footprint.geom_type == "MultiPolygon"
                  else [p.footprint]):
            s.append(f'<polygon points="{poly_str(q)}" fill="#e4e2dc" '
                     f'stroke="#a8a8a2" stroke-width="0.6"/>')
    fills = {"대교": "#e63946", "시범": "#f4a261", "화랑(현상태)": "#8338ec"}
    for name, prisms in (("대교", ctx["daegyo"]), ("시범", ctx["sibeom"]),
                         ("화랑(현상태)", ctx["hwarang"])):
        for pr in prisms:
            for q in (pr.footprint.geoms
                      if pr.footprint.geom_type == "MultiPolygon"
                      else [pr.footprint]):
                s.append(f'<polygon points="{poly_str(q)}" '
                         f'fill="{fills[name]}" fill-opacity="0.30" '
                         f'stroke="{fills[name]}" stroke-width="1.1"/>')
    legend_y = 78
    for name, col in fills.items():
        s.append(f'<rect x="18" y="{legend_y-10}" width="12" height="12" '
                 f'fill="{col}" fill-opacity="0.5" stroke="{col}"/>')
        s.append(f'<text x="36" y="{legend_y}" font-family="sans-serif" '
                 f'font-size="12" fill="#333">{name} 매싱</text>')
        legend_y += 18

    for row in rows:
        p = row["point"]
        x, y = P(p.x, p.y)
        if row["ok"]:
            s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.1" '
                     f'fill="#c7c7c1"/>')
    for row in rows:
        if row["ok"]:
            continue
        p = row["point"]
        x, y = P(p.x, p.y)
        col = GROUP_COLOR.get(row["cause"], "#adb5bd")
        r = 2.8 if row["cause"] != "기타/자체형상" else 2.2
        s.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{col}" '
                 f'stroke="#fff" stroke-width="0.6"/>')

    lx, ly = W - 210, head + 6
    s.append(f'<rect x="{lx-8:.0f}" y="{ly-18:.0f}" width="200" height="'
             f'{22*len(GROUP_COLOR)+14:.0f}" fill="#ffffff" fill-opacity="0.9" '
             'stroke="#ccc"/>')
    s.append(f'<text x="{lx:.0f}" y="{ly:.0f}" font-family="sans-serif" '
             'font-size="12" font-weight="bold" fill="#111">불충족 원인</text>')
    for i, (name, col) in enumerate(GROUP_COLOR.items()):
        yy = ly + 20 + i * 20
        s.append(f'<circle cx="{lx+6:.0f}" cy="{yy-4:.0f}" r="4.5" fill="{col}" '
                 'stroke="#fff" stroke-width="0.6"/>')
        s.append(f'<text x="{lx+18:.0f}" y="{yy:.0f}" font-family="sans-serif" '
                 f'font-size="11.5" fill="#333">{name}</text>')

    s.append(f'<g transform="translate({W-56:.0f},{head+4:.0f})">'
             '<line x1="0" y1="40" x2="0" y2="6" stroke="#111" stroke-width="2"/>'
             '<polygon points="0,0 -6,12 6,12" fill="#111"/>'
             '<text x="0" y="56" font-family="sans-serif" font-size="12" '
             'text-anchor="middle" fill="#111">N</text></g>')
    s.append("</svg>")
    path.write_text("\n".join(s), encoding="utf-8")
    return int(W), int(Hh)


def to_png(svg: Path, png: Path, w: int, h: int) -> bool:
    exe = next((c for c in CHROME if Path(c).exists()), None) or shutil.which("chromium")
    if not exe:
        return False
    url = "file://" + urllib.parse.quote(str(svg.resolve()))
    subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu",
                    f"--screenshot={png}", f"--window-size={w},{h}", url],
                   capture_output=True, timeout=120)
    return png.exists()


def write_report(path: Path, ctx: dict[str, Any], rows: list[dict[str, Any]]
                 ) -> None:
    n = len(rows)
    n_fail = sum(1 for r in rows if not r["ok"])
    by_cause = Counter(r["cause"] for r in rows if not r["ok"])
    L = [
        "# 대교·시범 신축 + 화랑 현상태 — 수광점 불충족 원인 아파트",
        "",
        "시나리오는 하나다: **대교 신축안 + 시범 신축안 + 화랑 현재 상태**",
        "(기존 10층 3개동, 재건축 전). 전후 비교가 아니라 이 구성에서 지금",
        "불충족인 점을 세고, 각 점을 누가(대교/시범/화랑/기타) 가리는지를",
        "반사실(그 하나만 빼고 다시 평가)로 정했다.",
        "",
        "## 전체",
        "",
        "| 항목 | 값 |", "|---|---:|",
        f"| 수광점 | {n:,}개 (건물 속에 박힌 점 {ctx['n_dropped']}개 제외한 재점검본) |",
        f"| 충족 | {n - n_fail:,}개 |",
        f"| **불충족** | **{n_fail:,}개** |",
        "",
        "## 불충족 원인별 분해",
        "",
        "| 원인(뺐을 때 가장 많이 회복되는 그룹) | 점 수 | 비율 |",
        "|---|---:|---:|",
    ]
    for name in ("대교", "시범", "화랑(현상태)", "복합(2개 이상)", "기타/자체형상"):
        c = by_cause.get(name, 0)
        L.append(f"| {name} | {c} | {c/n_fail*100:.1f}% |" if n_fail else
                 f"| {name} | 0 | — |")
    L += ["",
          "'기타/자체형상' = 세 재건축 대상 중 어느 하나를 빼도 회복되지 않는",
          "점이다 — 학교 자체 건물 형상(자기 그늘)이나 그 밖의 기존 건물이",
          "원인이라는 뜻이다.", ""]

    L += ["## 학교·동별 불충족 · 원인", "",
          "| 학교 / 구분 | 수광점 | 불충족 | 대교 | 시범 | 화랑 | 복합 | 기타 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    by_dong: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_dong[(r["point"].school, r["point"].dong)].append(r)
    for (school, dong), rs in sorted(by_dong.items()):
        fails = [r for r in rs if not r["ok"]]
        if not fails:
            continue
        cc = Counter(r["cause"] for r in fails)
        L.append(f"| {school} / {dong} | {len(rs)} | {len(fails)} | "
                 f"{cc.get('대교',0)} | {cc.get('시범',0)} | "
                 f"{cc.get('화랑(현상태)',0)} | {cc.get('복합(2개 이상)',0)} | "
                 f"{cc.get('기타/자체형상',0)} |")
    L.append("")

    L += ["## 산출물", "",
          "| 파일 | 내용 |", "|---|---|",
          "| `배치도.png/svg` | 지도. 점 색 = 불충족 원인 |",
          "| `수광점_불충족_원인_*.geojson` | 불충족 점 + 원인·회복시간(.qml 자동) |",
          "| `수광점_전체_*.geojson` | 전체 수광점(충족 포함) |",
          "| `매싱_*.geojson` | 대교·시범·화랑 매싱(참고) |", ""]
    path.write_text("\n".join(L), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    ctx = setup(args)
    print(f"■ 수광점 {len(ctx['receptors']):,}개 (제외 {ctx['n_dropped']}개) · "
          f"시점 {len(ctx['times'])}개")
    print(f"   대교 프리즘 {len(ctx['daegyo'])}개 · 시범 프리즘 {len(ctx['sibeom'])}개 "
          f"· 화랑 기존 {len(ctx['hwarang'])}개 · 기타 기존건물 {len(ctx['context'])}개")

    res = evaluate_all(ctx, args.step_min)
    rows = attribute(ctx, res)
    n_fail = sum(1 for r in rows if not r["ok"])
    print(f"\n■ 불충족 {n_fail}개 / {len(rows)}개")
    by_cause = Counter(r["cause"] for r in rows if not r["ok"])
    for name in ("대교", "시범", "화랑(현상태)", "복합(2개 이상)", "기타/자체형상"):
        print(f"   {name:<14} {by_cause.get(name, 0):>4}개")

    write_outputs(args, ctx, rows)
    write_report(args.outdir / "report.md", ctx, rows)
    args.outdir.mkdir(parents=True, exist_ok=True)
    svg = args.outdir / "배치도.svg"
    w, h = plan_svg(svg, ctx, rows)
    png = args.outdir / "배치도.png"
    ok = to_png(svg, png, w, h)
    print(f"\n   배치도 → {svg}" + (f" · {png}" if ok else " (PNG 실패)"))
    print(f"   → {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
