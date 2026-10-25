#!/usr/bin/env python3
"""화랑 일조 — 우리 판정 vs 일조 분석 업체(2026.10) 판정 비교.

업체 보고서(「화랑아파트 재건축정비사업 교육환경평가 일조 영향 분석」,
Sanalyst 5.0)와 우리 모형(hwarang_massing_study)의 차이가 결과를 얼마나
바꾸는지 같은 기하로 잰다.

  우리(종전)  한국표준시(KST) 08–16시 · 교사·운동장 모두 「연속 2h 또는 총 4h」
  업체        진태양시 08–16시 · 교사 「총 4h 또는 학교급 시간대 연속 2h」
              운동장 「총 2h 또는 학교급 시간대 연속 1h」
              학교급 연속 시간대: 초 09–13 · 중 09–14 · 고 09–15
              분석 대상: 여의도초 · 여의도중 · 여의도여고 (여의도고 제외)

AL_D010 원본 없이도 돌도록 입력을 산출물에서 다시 세운다.
  · 차폐물: outputs/hwarang_detail_A_2026/화랑A_상세+주변.obj 의 주변·학교·
    대교·시범 신축 프리즘(윗면 삼각형을 합쳐 바닥형상 + 높이)
  · 수광점: 최적안_수광점_전체 GeoJSON(1,074점). 창면 방위는 school_receptors
    CSV 에서, 없으면 가장 가까운 학교 건물 외곽선의 바깥 법선으로
  · 사업 이전: 화랑 기존 3동(9층 30m)

먼저 종전 판정으로 A안 결과(신규 불충족 73 · 건물 33)를 재현해 입력이
맞는지 확인한 뒤, 업체 판정으로 바꿔 다시 잰다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from shapely.geometry import Point, Polygon, shape
from shapely.ops import unary_union

import hwarang_massing_study as H
from daegyo_site_polygons import solar_position

ORIGIN = (194327.70, 546974.70)
OBJ = Path("outputs/hwarang_detail_A_2026/화랑A_상세+주변.obj")
RECEPTORS = Path("outputs/hwarang_hr_bld_nopod_2026/최적안_수광점_전체_epsg5186.geojson")
CSV_DIR = Path("outputs/school_receptors")
LAT, LON = 37.5228, 126.9346
WINDOW = {"여의도초등학교": (9, 13), "여의도중학교": (9, 14),
          "여의도여자고등학교": (9, 15), "여의도고등학교": (9, 15)}
SHORT = {"여의도초등학교": "초", "여의도중학교": "중", "여의도여자고등학교": "여고",
         "여의도고등학교": "고"}


def load_context(path: Path = OBJ, mats=("context", "context_new", "school")):
    V, F, cur = [], [], None
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            if line.startswith("v "):
                V.append([float(t) for t in line.split()[1:4]])
            elif line.startswith("usemtl"):
                cur = line.split()[1]
            elif line.startswith("f ") and cur in mats:
                F.append(([int(t) - 1 for t in line.split()[1:4]], cur))
    V = np.asarray(V)
    tops = defaultdict(list)
    for (a, b, c), m in F:
        z = V[[a, b, c], 2]
        if z.max() - z.min() < 1e-6 and z[0] > 0.5:
            tri = Polygon(V[[a, b, c], :2] + ORIGIN)
            if tri.area > 1e-6:
                tops[(round(float(z[0]), 2), m)].append(tri.buffer(0.01))
    prisms = []
    for (z, m), tris in tops.items():
        u = unary_union(tris).buffer(-0.01)
        for p in getattr(u, "geoms", [u]):
            if p.area > 4:
                prisms.append(H.Prism(p, z, m))
    return prisms


def load_receptors(prisms):
    feats = json.loads(RECEPTORS.read_text(encoding="utf-8"))["features"]
    normals = {}
    for p in CSV_DIR.glob("*_receptors.csv"):
        with p.open(encoding="utf-8-sig") as fp:
            for r in csv.DictReader(fp):
                normals[(round(float(r["X"]), 1), round(float(r["Y"]), 1))] = float(r["창면방위"])
    schools = [p.footprint for p in prisms if p.label == "school"]
    recs, meta = [], []
    n_csv = n_geom = 0
    for f in feats:
        pr = f["properties"]
        x, y = f["geometry"]["coordinates"][:2]
        horiz = pr["kind"] == "운동장 지반"
        nz = 0.0
        if not horiz:
            key = (round(x, 1), round(y, 1))
            if key in normals:
                nz = normals[key]
                n_csv += 1
            else:
                pt = Point(x, y)
                fp = min(schools, key=lambda g: g.exterior.distance(pt))
                ring = fp.exterior
                s = ring.project(pt)
                q0, q1 = ring.interpolate(max(s - 0.5, 0)), ring.interpolate(s + 0.5)
                tx, ty = q1.x - q0.x, q1.y - q0.y
                # 바깥 법선: 점이 외곽선 밖이면 외곽선→점 방향
                c = ring.interpolate(s)
                vx, vy = x - c.x, y - c.y
                if math.hypot(vx, vy) < 0.05:
                    vx, vy = ty, -tx
                nz = math.degrees(math.atan2(vx, vy)) % 360
                n_geom += 1
        recs.append(H.Receptor(x, y, float(pr["z_m"]), nz, pr["dong"],
                               int(pr["floor"]), horiz))
        meta.append(pr)
    return recs, meta, n_csv, n_geom


def times_for(mode: str, step_min: int = 10):
    """mode='kst' → 시계 08–16(KST), 'solar' → 진태양시 08–16.
    반환 (판정용 시각, 고도, 방위)."""
    off = 0.0
    if mode == "solar":
        n = 334 + 22
        b = math.radians(360.0 * (n - 81) / 364.0)
        eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
        off = -(4.0 * (LON - 135.0) + eot) / 60.0     # KST = 진태양시 + off
    out = []
    for i in range(int(8 * 60 / step_min) + 1):
        t = 8.0 + i * step_min / 60.0
        alt, az = solar_position(LAT, LON, 12, 22, t + off)
        if alt > 0.5:
            out.append((t, alt, az))
    return out


def judge(flags, times, step, school, horizontal, rule):
    def run(lo, hi):
        best = cur = 0
        for f, (t, _a, _z) in zip(flags, times):
            if lo <= t <= hi and f:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        return best * step

    total = sum(flags) * step
    if rule == "ours":
        return run(8, 16) >= 2.0 or total >= 4.0
    lo, hi = WINDOW[school]
    if horizontal:
        return total >= 2.0 or run(lo, hi) >= 1.0
    return total >= 4.0 or run(lo, hi) >= 2.0


def prisms_from_geojson(path: Path, label: str):
    out = []
    for f in json.loads(path.read_text(encoding="utf-8"))["features"]:
        h = f["properties"].get("height_m")
        if h:
            g = shape(f["geometry"])
            for p in getattr(g, "geoms", [g]):
                out.append(H.Prism(p, float(h), label))
    return out


def scaled(prisms, height):
    return [H.Prism(p.footprint, height, p.label) for p in prisms]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path("outputs/hwarang_consultant_check_2026"))
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)

    ctx = load_context()
    recs, meta, n_csv, n_geom = load_receptors(ctx)
    print(f"차폐물 {len(ctx)}개 · 수광점 {len(recs)}개(창면방위 CSV {n_csv} · 기하 {n_geom})")

    base = prisms_from_geojson(Path("outputs/hwarang/hwarang_scheme_Z1.geojson"), "화랑 기존")
    A = prisms_from_geojson(Path("outputs/hwarang_hr_bld_nopod_2026/최적안_매싱_epsg5186.geojson"), "A")
    # A안을 설계사 층고(1층 필로티 5.0 · 기준층 2.85 · 최상부 3.25+2.95, 옥탑 5.8 은 코어만)로
    h_map = 5.0 + 48 * 2.85 + 3.25 + 2.95 + 1.2
    f20 = Path("outputs/hwarang_20f_2026/최적안_매싱_epsg5186.geojson")
    scen = {"사업이전(화랑 기존)": base, "A안 51층 172.3m": A,
            f"A안 51층 층고 2.85m({h_map:.1f}m)": scaled(A, h_map)}
    if f20.exists():
        scen["20층안"] = prisms_from_geojson(f20, "20층")

    rows = []
    results = {}
    for mode in ("kst", "solar"):
        times = times_for(mode)
        masks = H.build_context_masks(ctx, recs, times)
        for name, pr in scen.items():
            flags = H.sunlit_flags(recs, pr, times, masks)
            results[(mode, name)] = flags
        print(f"  시각 {mode}: {len(times)}개 표본")

    def counts(mode, name, rule, base_name="사업이전(화랑 기존)"):
        times = times_for(mode)
        f0, f1 = results[(mode, base_name)], results[(mode, name)]
        c = Counter()
        for r, m, a0, a1 in zip(recs, meta, f0, f1):
            sch = m["school"]
            kind = "운동장" if r.horizontal else "교사"
            p0 = judge(a0, times, 1 / 6, sch, r.horizontal, rule)
            p1 = judge(a1, times, 1 / 6, sch, r.horizontal, rule)
            c[(sch, kind, "n")] += 1
            c[(sch, kind, "fail0")] += not p0
            c[(sch, kind, "fail1")] += not p1
            c[(sch, kind, "new")] += p0 and not p1
        return c

    combos = [("종전(KST·우리 판정)", "kst", "ours"),
              ("KST·업체 판정", "kst", "consult"),
              ("진태양시·우리 판정", "solar", "ours"),
              ("업체 방식(진태양시·업체 판정)", "solar", "consult")]
    lines = []
    for lab, mode, rule in combos:
        for name in scen:
            if name.startswith("사업이전"):
                continue
            c = counts(mode, name, rule)
            row = {"판정": lab, "안": name}
            for sch in WINDOW:
                for kind in ("교사", "운동장"):
                    row[f"{SHORT[sch]}_{kind}_new"] = c[(sch, kind, "new")]
                    row[f"{SHORT[sch]}_{kind}_fail0"] = c[(sch, kind, "fail0")]
                    row[f"{SHORT[sch]}_{kind}_n"] = c[(sch, kind, "n")]
            all4 = sum(c[(s, k, "new")] for s in WINDOW for k in ("교사", "운동장"))
            bld4 = sum(c[(s, "교사", "new")] for s in WINDOW)
            three = [s for s in WINDOW if s != "여의도고등학교"]
            all3 = sum(c[(s, k, "new")] for s in three for k in ("교사", "운동장"))
            bld3 = sum(c[(s, "교사", "new")] for s in three)
            row.update({"전체4교_new": all4, "교사4교_new": bld4,
                        "전체3교_new": all3, "교사3교_new": bld3})
            rows.append(row)
            lines.append(f"{lab:28s} {name:26s} 4개교 {all4:3d}(교사 {bld4:3d}) · "
                         f"초중여고 {all3:3d}(교사 {bld3:3d}) · "
                         + " ".join(f"{SHORT[s]} 교사{c[(s,'교사','new')]}/운{c[(s,'운동장','new')]}"
                                    for s in WINDOW))
    print("\n".join(lines))
    with (a.out / "판정비교.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"→ {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
