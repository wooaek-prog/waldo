#!/usr/bin/env python3
"""학교 배치도(래스터) 좌표등록 → 운동장 경계 추출.

사용자가 준 '학교 위치 배치도'는 학교마다 색을 칠하고 **운동장을 해치(격자무늬)
로, 건물을 단색으로** 표시한다. 치수선·좌표는 없지만 건물 외곽선이 AL_D010 과
같은 도형이므로, **건물 선화에 맞춰 축척·평행이동을 역산**하면 운동장 경계를
실좌표로 읽어낼 수 있다.

    1. 화소를 대표색(학교색·검정 선화·흰 바탕 …)으로 분류한다.
    2. AL_D010 건물 외곽선을 화소로 투영해, 지도의 검정 선화와 가장 잘 겹치는
       (축척 s, 원점 ox·oy) 를 탐색한다. 회전은 0으로 둔다(정북 배치도).
    3. 학교색 화소에서 **건물 폴리곤을 뺀 나머지**가 운동장 해치다. 라벨 박스로
       끊긴 부분은 파란 라벨 화소를 함께 이어 붙여 메운다.
    4. 남은 화소를 블록 장축(52°)에 맞춰 투영해 1~99 백분위로 사각형을 잡는다
       (해치선·라벨 때문에 최소외접사각형은 과대평가된다).

한계: 래스터 계측이라 경계 정밀도가 **±3m 안팎**이다. 격자 수는 이 도면이
아니라 학교별 옥외체육장 분석지점도에서 온다.

    python3 school_site_map.py --map <배치도.png> --buildings <AL_D010.gpkg>
"""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

import hwarang_massing_study as H
from daegyo_school_sunlight import load_named_buildings

# 대표색 – 배치도 범례
PALETTE: dict[str, tuple[int, int, int]] = {
    "green": (194, 227, 183),    # 서울여의도초등학교
    "yellow": (243, 202, 86),    # 여의도중학교
    "salmon": (244, 128, 128),   # 여의도고등학교
    "magenta": (236, 28, 123),   # 여의도여자고등학교
    "purple": (215, 190, 222),   # 사업대상지 내 기존동
    "white": (255, 255, 255),
    "grey": (236, 235, 235),
    "black": (0, 0, 0),
    "blue": (16, 41, 140),       # 라벨 박스
    "cyan": (185, 231, 245),
    "red": (236, 0, 17),
}
SCHOOL_COLOUR = {"30-1": "salmon", "40-1": "yellow",
                 "40-2": "magenta", "40-3": "green"}
# 운동장을 찾을 화소창 (y0, y1, x0, x1). 같은 색이 지도 다른 곳(공원 녹지 등)에도
# 쓰이므로 학교 주변으로 잘라 준다. 456x456 원본 기준.
WINDOW = {"30-1": (15, 135, 75, 205),
          "40-1": (118, 235, 180, 275),
          "40-2": (250, 345, 160, 285),
          "40-3": (222, 318, 278, 382)}
BLOCK_AZ = 142.0          # 여의도 블록 장축 52° 의 직교
ORIGIN = (194022.1, 546831.0)   # 화소→미터 변환의 기준점(임의)


def classify_pixels(im: Image.Image) -> tuple[np.ndarray, list[str]]:
    a = np.array(im.convert("RGB")).astype(float)
    names = list(PALETTE)
    ref = np.array([PALETTE[k] for k in names], dtype=float)
    d = np.linalg.norm(a[:, :, None, :] - ref[None, None, :, :], axis=3)
    return d.argmin(axis=2), names


def _dilate(m: np.ndarray, k: int = 1) -> np.ndarray:
    out = m.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            out |= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def _erode(m: np.ndarray, k: int = 1) -> np.ndarray:
    out = m.copy()
    for dy in range(-k, k + 1):
        for dx in range(-k, k + 1):
            out &= np.roll(np.roll(m, dy, 0), dx, 1)
    return out


def _components(m: np.ndarray) -> list[list[tuple[int, int]]]:
    h, w = m.shape
    seen = np.zeros_like(m)
    out: list[list[tuple[int, int]]] = []
    for y, x in zip(*np.nonzero(m)):
        if seen[y, x]:
            continue
        q = deque([(y, x)])
        seen[y, x] = True
        pix = []
        while q:
            cy, cx = q.popleft()
            pix.append((cy, cx))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((ny, nx))
        out.append(pix)
    out.sort(key=len, reverse=True)
    return out


def fit_transform(idx: np.ndarray, names: Sequence[str], im: Image.Image,
                  buildings: Sequence[dict[str, Any]], extent,
                  ) -> tuple[float, float, float]:
    """건물 선화에 맞춰 (축척 m/px, 원점화소 ox, oy) 를 찾는다."""
    a = np.array(im.convert("RGB")).astype(int)
    dark = (a.sum(2) / 3 < 150) & (idx != names.index("blue"))
    prox = np.zeros(dark.shape)
    prox[_dilate(dark, 2)] = 0.25
    prox[_dilate(dark, 1)] = 0.60
    prox[dark] = 1.00

    pts = []
    for r in buildings:
        g = r["geom"]
        if not g.intersects(extent):
            continue
        for p in (g.geoms if g.geom_type.startswith("Multi") else [g]):
            ring = p.exterior
            n = max(8, int(ring.length / 3))
            for i in range(n):
                c = ring.interpolate(i / n, normalized=True)
                pts.append((c.x, c.y))
    pts = np.array(pts)
    X = pts[:, 0] - ORIGIN[0]
    Y = pts[:, 1] - ORIGIN[1]
    h, w = prox.shape

    def score(s: float, ox: float, oy: float) -> float:
        px = np.rint(X / s + ox).astype(int)
        py = np.rint(oy - Y / s).astype(int)
        ok = (px >= 0) & (px < w) & (py >= 0) & (py < h)
        if ok.sum() < 500:
            return 0.0
        return prox[py[ok], px[ok]].sum() / ok.sum()

    best = (0.0, 1.0, 0.0, 0.0)
    for s in np.arange(0.80, 1.31, 0.02):
        for ox in np.arange(-40, 121, 3.0):
            for oy in np.arange(350, 561, 3.0):
                v = score(s, ox, oy)
                if v > best[0]:
                    best = (v, s, ox, oy)
    _v, s, ox, oy = best
    for step in (1.0, 0.5, 0.25):
        cand = [(score(s + ds, ox + dx, oy + dy), s + ds, ox + dx, oy + dy)
                for ds in np.arange(-0.02, 0.021, 0.005)
                for dx in np.arange(-3, 3.01, step)
                for dy in np.arange(-3, 3.01, step)]
        cand.sort(reverse=True)
        _v, s, ox, oy = cand[0]
    print(f"   좌표등록: 축척 {s:.3f} m/화소 · 원점화소 ({ox:.2f}, {oy:.2f}) · "
          f"선화 일치도 {_v:.3f}")
    return float(s), float(ox), float(oy)


def playground(idx: np.ndarray, names: Sequence[str], jibun: str,
               bmask: np.ndarray, s: float, ox: float, oy: float,
               az: float = BLOCK_AZ, trim: float = 1.0) -> Polygon | None:
    """학교색 − 건물 = 운동장 해치. 블록축 정렬 사각형으로 돌려준다."""
    h, w = idx.shape
    y0, y1, x0, x1 = WINDOW[jibun]
    win = np.zeros((h, w), bool)
    win[y0:y1, x0:x1] = True
    col = (idx == names.index(SCHOOL_COLOUR[jibun]))
    lbl = (idx == names.index("blue"))
    m = _erode(_dilate((col | lbl) & win & ~_dilate(bmask, 2), 4), 4)
    comps = _components(m)
    if not comps:
        return None
    sel = np.zeros_like(m)
    for y, x in comps[0]:
        sel[y, x] = True
    sel &= col                      # 사각형은 학교색 화소로만 잰다
    ys, xs = np.nonzero(sel)
    if len(ys) < 50:
        return None
    xm = ORIGIN[0] + (xs - ox) * s
    ym = ORIGIN[1] - (ys - oy) * s
    u = (math.sin(math.radians(az)), math.cos(math.radians(az)))
    v = (math.sin(math.radians(az - 90)), math.cos(math.radians(az - 90)))
    pu = xm * u[0] + ym * u[1]
    pv = xm * v[0] + ym * v[1]
    a, b = np.percentile(pu, [trim, 100 - trim])
    c, d = np.percentile(pv, [trim, 100 - trim])
    corners = [(a, c), (b, c), (b, d), (a, d)]
    return Polygon([(cu * u[0] + cv * v[0], cu * u[1] + cv * v[1])
                    for cu, cv in corners])


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--map", type=Path, required=True, help="배치도 이미지")
    p.add_argument("--buildings", type=Path, required=True)
    p.add_argument("--daegyo", type=Path,
                   default=Path("outputs/daegyo/daegyo_buildings_epsg5186.geojson"))
    p.add_argument("--outdir", type=Path,
                   default=Path("outputs/school_compliance/playground"))
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    im = Image.open(args.map)
    idx, names = classify_pixels(im)
    buildings = load_named_buildings(args.buildings)
    extent = box(193950, 546750, 194600, 547450)
    print("■ 배치도 좌표등록")
    s, ox, oy = fit_transform(idx, names, im, buildings, extent)

    h, w = idx.shape
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    for r in buildings:
        g = r["geom"]
        if not g.intersects(extent):
            continue
        for p in (g.geoms if g.geom_type.startswith("Multi") else [g]):
            d.polygon([(ox + (x - ORIGIN[0]) / s, oy - (y - ORIGIN[1]) / s)
                       for x, y in p.exterior.coords], fill=255)
    bmask = np.array(img) > 0

    print("\n■ 운동장 경계(배치도 해치영역)")
    feats = []
    for jibun in sorted(WINDOW):
        pg = playground(idx, names, jibun, bmask, s, ox, oy)
        if pg is None:
            print(f"   {jibun}: 추출 실패")
            continue
        e = list(pg.exterior.coords)
        sides = [math.dist(e[i], e[i + 1]) for i in range(4)]
        print(f"   {jibun}: {pg.area:,.0f}㎡ · {sides[0]:.1f} x {sides[1]:.1f} m · "
              f"중심 ({pg.centroid.x:.1f}, {pg.centroid.y:.1f})")
        feats.append({"type": "Feature", "properties": {
            "jibun": jibun, "area_m2": round(pg.area, 1),
            "source": "배치도 해치영역(래스터 계측)"},
            "geometry": {"type": "Polygon", "coordinates": [
                [[round(x, 2), round(y, 2)] for x, y in e]]}})
    out = args.outdir / "운동장_배치도경계_epsg5186.geojson"
    out.write_text(json.dumps(
        {"type": "FeatureCollection",
         "crs": {"type": "name", "properties": {"name": "EPSG:5186"}},
         "features": feats}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n   → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
