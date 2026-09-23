#!/usr/bin/env python3
"""화랑 확정안(A안) 조감도 밑그림 — 주변 건물 포함.

조감도 제작용 밑그림을 만든다. 화랑 A안(51층 1개동, 저층부 없음)과
**대교 신축안·시범 신축안**, 주변 학교, 반경 안의 기존 건물(AL_D010 높이)을
실좌표 그대로 3D 각기둥으로 세워 여러 방향에서 축측투영으로 찍는다.

산출
    조감_<방향>_주석.png   제목·라벨·범례·동지 그림자가 들어간 검토용
    조감_<방향>_원판.png   글자 없는 밑그림 — 렌더링/합성 작업용
    조감_모형.obj/.mtl      같은 장면의 3D 모형(대지 중심 원점, m 단위, Z-up)
                            — SketchUp·Blender·Rhino 로 열어 조감도를 렌더링
    조감_모형_건물목록.csv  모형에 들어간 건물(구분·높이·출처)

주변 기존 건물 높이는 AL_D010 의 높이/층수에서 복원한 값이다(누락분은
층수×3m 추정). 대교·화랑·시범 대지의 **기존** 건물은 신축안으로
대체되므로 뺀다.

    python3 hwarang_birdseye_2026.py --buildings <AL_D010.gpkg>
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Sequence

from shapely.geometry import Polygon, shape
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

import hwarang_birdseye as B
import hwarang_massing_study as H
from apartment_attribution import SIBEOM_JIBUN, load_prisms_from_geojson
from daegyo_school_sunlight import (
    DAEGYO_JIBUN, HWARANG_JIBUN, load_named_buildings, resolved_height,
    school_label, school_parcel_rows,
)

CHROME = ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
          "/opt/pw-browsers/chromium/chrome-linux/chrome")
B.PALETTE.setdefault("sibeom", ("#b08968", "#c9a27e", "#977455", "#4d3a2a"))
LEGEND = [("hwarang", "화랑 신축(A안 51층)"), ("daegyo", "대교 신축안"),
          ("sibeom", "시범 신축안"), ("school", "학교"),
          ("context", "기존 건물")]
# (키, 카메라 방위 = 보는 사람이 선 쪽, 앙각, 이름, 그림자 시각)
VIEWS = [
    ("남서", 225.0, 32.0, "남서측(대교 쪽)에서", 12.5),
    ("남동", 135.0, 32.0, "남동측에서", 12.5),
    ("남", 180.0, 28.0, "정남측에서", 12.5),
    ("북동", 45.0, 32.0, "북동측(여의도초 쪽)에서", 12.5),
    ("북서", 315.0, 32.0, "북서측(학교 쪽)에서", 12.5),
    ("고각", 200.0, 60.0, "고각(배치 전경)", 10.0),
]


def explode(g):
    return list(g.geoms) if g.geom_type == "MultiPolygon" else [g]


def to_png(svg: Path, png: Path, w: int, h: int) -> bool:
    exe = next((c for c in CHROME if Path(c).exists()), None) \
        or shutil.which("chromium")
    if not exe:
        return False
    url = "file://" + urllib.parse.quote(str(svg.resolve()))
    subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu",
                    "--hide-scrollbars", f"--screenshot={png}",
                    f"--window-size={w},{h}", url],
                   capture_output=True, timeout=180)
    return png.exists()


def build_scene(args) -> tuple[list[B.Solid], list[dict], Polygon]:
    buildings = load_named_buildings(args.buildings)
    hw = load_prisms_from_geojson(args.hwarang, "화랑")
    if not hw:
        raise SystemExit(f"화랑 매싱을 못 읽었다: {args.hwarang}")
    centre = unary_union([p.footprint for p in hw]).centroid
    daegyo = H.load_daegyo(args.daegyo)
    sibeom = load_prisms_from_geojson(args.sibeom, "시범", layer_filter="신축동")

    solids: list[B.Solid] = []
    rows: list[dict] = []

    def add(poly, h, kind, name, src, label=""):
        # 트레이싱 윤곽의 잔톱니를 0.8m 로 다듬는다 — 그대로 두면 벽면이
        # 수십 장의 가는 띠로 쪼개져 조감도에 세로 줄무늬가 생긴다.
        # 시범 신축안은 배치도 래스터에서 딴 계단형 윤곽(톱니 1~3m)이라 더 크게.
        tol = 1.5 if kind == "sibeom" else 0.8
        poly = poly.simplify(tol, preserve_topology=True)
        if poly.is_empty or poly.geom_type != "Polygon":
            return
        solids.append(B.Solid(poly, h, kind, label))
        rows.append({"구분": name, "종류": kind, "높이_m": round(h, 1),
                     "바닥면적_㎡": round(poly.area, 1), "출처": src,
                     "poly": poly})

    top = max(p.top_m for p in hw)
    for p in hw:
        for poly in explode(p.footprint):
            add(poly, p.top_m, "hwarang", "화랑 A안", "hwarang_hr_bld_nopod_2026",
                f"화랑 A안 51F · {top:.0f}m" if p.top_m == top else "")
    for p in daegyo:
        if p.footprint.centroid.distance(centre) > args.radius:
            continue
        for poly in explode(p.footprint):
            add(poly, p.top_m, "daegyo", f"대교 {p.label}", "대교 신축안")
    for p in sibeom:
        if p.footprint.centroid.distance(centre) > args.radius:
            continue
        for poly in explode(p.footprint):
            add(poly, p.top_m, "sibeom", f"시범 {p.label}", "시범 신축안")
    for kind, lab in (("daegyo", "대교 신축"), ("sibeom", "시범 신축")):
        cand = [s for s in solids if s.kind == kind]
        if cand:
            max(cand, key=lambda s: (s.top_m, s.footprint.area)).label = lab

    dg_centre = unary_union([p.footprint for p in daegyo]).centroid
    schools = school_parcel_rows(buildings, dg_centre, args.school_radius)
    school_ids = set()
    for jibun, srows in schools.items():
        name = school_label(jibun, srows).split(" ", 1)[1]
        near = [r for r in srows
                if r["geom"].centroid.distance(centre) <= args.radius]
        for r in near:
            for poly in explode(r["geom"]):
                if poly.area < 20:
                    continue
                add(poly, r["height_m"], "school", name, "AL_D010")
            school_ids.add(id(r["geom"]))
        if near:
            big = max(near, key=lambda r: r["geom"].area)
            cand = [s for s in solids if s.kind == "school"
                    and big["geom"].contains(s.footprint.representative_point())]
            if cand:
                max(cand, key=lambda s: s.footprint.area).label = name

    skip = {HWARANG_JIBUN, DAEGYO_JIBUN, SIBEOM_JIBUN}
    for row in buildings:
        if row["jibun"] in skip or id(row["geom"]) in school_ids:
            continue
        if row["geom"].centroid.distance(centre) > args.radius:
            continue
        h = resolved_height(row)
        src = "AL_D010"
        if h <= 0:
            h, src = 4.0, "AL_D010(높이 미상 — 1층 4m 가정)"
        for poly in explode(row["geom"]):
            if poly.area < 30:
                continue
            add(poly, h, "context", "기존 건물", src)
    return solids, rows, centre


def write_obj(outdir: Path, rows: list[dict], centre) -> None:
    """대지 중심 원점(x=동, y=북, z=위, m) OBJ + MTL."""
    mtl = {k: B.PALETTE[k][1] for k in B.PALETTE}
    with (outdir / "조감_모형.mtl").open("w", encoding="utf-8") as fp:
        for k, hexc in mtl.items():
            r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
            fp.write(f"newmtl {k}\nKd {r:.3f} {g:.3f} {b:.3f}\nKa 0 0 0\n"
                     f"Ks 0 0 0\nd 1\nillum 1\n\n")
    lines = [f"# 화랑 A안 조감 모형 — 원점 E{centre.x:.2f} N{centre.y:.2f}"
             " (EPSG:5186), 단위 m, Z-up", "mtllib 조감_모형.mtl"]
    v = 0
    for i, r in enumerate(rows):
        poly = orient(r["poly"].simplify(0.05), sign=1.0)
        ring = list(poly.exterior.coords)[:-1]
        n = len(ring)
        if n < 3:
            continue
        h = r["높이_m"]
        lines.append(f"o {r['종류']}_{i:04d}")
        lines.append(f"usemtl {r['종류']}")
        for x, y in ring:
            lines.append(f"v {x - centre.x:.3f} {y - centre.y:.3f} 0.000")
        for x, y in ring:
            lines.append(f"v {x - centre.x:.3f} {y - centre.y:.3f} {h:.3f}")
        bot = list(range(v + 1, v + n + 1))
        topi = list(range(v + n + 1, v + 2 * n + 1))
        lines.append("f " + " ".join(str(k) for k in reversed(bot)))
        lines.append("f " + " ".join(str(k) for k in topi))
        for k in range(n):
            a, b2 = bot[k], bot[(k + 1) % n]
            c, d = topi[(k + 1) % n], topi[k]
            lines.append(f"f {a} {b2} {c} {d}")
        v += 2 * n
    (outdir / "조감_모형.obj").write_text("\n".join(lines), encoding="utf-8")
    with (outdir / "조감_모형_건물목록.csv").open("w", encoding="utf-8-sig",
                                              newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["번호", "구분", "종류", "높이_m", "바닥면적_㎡", "출처",
                    "중심_E", "중심_N"])
        for i, r in enumerate(rows):
            c = r["poly"].centroid
            w.writerow([i, r["구분"], r["종류"], r["높이_m"], r["바닥면적_㎡"],
                        r["출처"], round(c.x, 2), round(c.y, 2)])


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--hwarang", type=Path,
                   default=Path("outputs/hwarang_hr_bld_nopod_2026/최적안_매싱_epsg5186.geojson"))
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--sibeom", type=Path,
                   default=Path("outputs/sibeom/sibeom_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path, default=Path("outputs/hwarang_birdseye_A_2026"))
    p.add_argument("--radius", type=float, default=380.0)
    p.add_argument("--school-radius", type=float, default=350.0)
    p.add_argument("--width", type=float, default=2000.0)
    a = p.parse_args(argv)
    a.outdir.mkdir(parents=True, exist_ok=True)

    solids, rows, centre = build_scene(a)
    from collections import Counter
    print(f"장면: {len(solids)}개 입체 · " + ", ".join(
        f"{k} {v}" for k, v in Counter(s.kind for s in solids).items()))

    from pyproj import CRS, Transformer
    lon, lat = Transformer.from_crs(CRS.from_epsg(5186), CRS.from_epsg(4326),
                                    always_xy=True).transform(centre.x, centre.y)
    times = H.sun_track(12, 22, lat, lon, step_min=10)
    hw = [s for s in solids if s.kind == "hwarang"]
    sub = (f"화랑 A안: 51층 1개동 · 기준층 737㎡(59.5×12.4m) · 높이 "
           f"{max(s.top_m for s in hw):.1f}m · 저층부 없음 · 용적률 400% · "
           f"건폐율 8.6% — 주변: 대교·시범 신축안, 학교, 기존 건물(반경 {a.radius:.0f}m)")
    for key, caz, cel, name, clock in VIEWS:
        t = min(times, key=lambda x: abs(x[0] - clock))
        sun = (t[2], t[1])
        for variant in ("주석", "원판"):
            ann = variant == "주석"
            svg = a.outdir / f"조감_{key}_{variant}.svg"
            w, h = B.render(
                svg, solids, caz, cel,
                f"화랑 A안 조감 — {name}",
                sub + (f" · 그림자 동지 {int(t[0]):02d}:"
                       f"{int(round((t[0] % 1) * 60)):02d}" if ann else ""),
                sun if ann else None, width=a.width,
                legend=LEGEND if ann else [], labels=ann, header=ann,
                per_solid=True)
            ok = to_png(svg, svg.with_suffix(".png"), w, h)
            print(f"  {svg.with_suffix('.png' if ok else '.svg').name}")
    write_obj(a.outdir, rows, centre)
    print(f"  조감_모형.obj ({len(rows)}개 건물) · 조감_모형_건물목록.csv")
    print(f"→ {a.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
