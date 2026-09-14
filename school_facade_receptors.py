#!/usr/bin/env python3
"""학교 교사동의 '실제 창면' 수광점 생성.

기존 일반식(hwarang_massing_study.make_receptors)은 외벽 전체를 5m 간격으로
쪼개고 남향계열(방위 90~270°) 벽면이면 모두 수광점을 두었다. 그래서
    · 교실이 없는 **단부벽·계단실**에도 수광점이 생기고
    · 층고 3.5m·창중심 1.2m 를 모든 학교에 똑같이 적용
하는 한계가 있었다.

여기서는 정면도에서 읽은 값(data/school_facades.json)으로
    · **지정한 파사드 방위 ±tol 구간의 외벽에만** 수광점을 두고
    · 층고·창 하단/상단을 건물별로 적용해 **창 중앙 높이**를 쓰고
    · 계단실 등 교실창이 없는 구간을 뺀다.
정의가 없는 학교는 기존 일반식으로 그대로 생성한다(점진 적용).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

from shapely.geometry import Point

import hwarang_massing_study as H

DEFAULT_SPEC = Path(__file__).with_name("data") / "school_facades.json"


def load_spec(path: Path = DEFAULT_SPEC) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def facade_segments(geom, azimuth: float, tol_deg: float
                    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """바깥 법선이 azimuth ±tol 안에 드는 외벽 구간 목록.

    반환 순서는 파사드를 따라 한 방향으로 정렬된다(exclude_spans 의 기준선).
    """
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    out = []
    for poly in polys:
        coords = list(poly.exterior.coords)
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            ex, ey = x1 - x0, y1 - y0
            n = math.hypot(ex, ey)
            if n < 0.2:
                continue
            nx, ny = ey / n, -ex / n
            if poly.contains(Point(mx + nx * 0.3, my + ny * 0.3)):
                nx, ny = -nx, -ny
            az = math.degrees(math.atan2(nx, ny)) % 360
            if abs((az - azimuth + 180) % 360 - 180) <= tol_deg:
                out.append(((x0, y0), (x1, y1)))
    # 파사드 진행 방향(장변 방위)으로 정렬
    if out:
        ang = math.radians(azimuth - 90.0)
        ux, uy = math.sin(ang), math.cos(ang)
        out.sort(key=lambda s: (s[0][0] + s[1][0]) / 2 * ux
                 + (s[0][1] + s[1][1]) / 2 * uy)
    return out


def facade_axis(spec: dict[str, Any]) -> tuple[float, float]:
    """파사드 전개축 단위벡터. 방위 = facade_azimuth − 90° 방향으로 증가."""
    ang = math.radians(spec["facade_azimuth"] - 90.0)
    return math.sin(ang), math.cos(ang)


def facade_frame(row: dict[str, Any], spec: dict[str, Any]):
    """파사드 전개 좌표계.

    반환 (ux, uy, s0, total) – 임의의 점 (x,y) 의 전개거리는
        s = (x*ux + y*uy) − s0   (0 ≤ s ≤ total)
    구간의 저장 순서·방향과 무관하게 항상 같은 값이 되도록,
    파사드 위 모든 끝점의 투영 최솟값을 원점으로 잡는다.
    """
    ux, uy = facade_axis(spec)
    segs = facade_segments(row["geom"], spec["facade_azimuth"],
                           spec.get("tol_deg", 30.0))
    if not segs:
        return ux, uy, 0.0, 0.0
    proj = [p[0] * ux + p[1] * uy for seg in segs for p in seg]
    return ux, uy, min(proj), max(proj) - min(proj)


def receptors_from_spec(row: dict[str, Any], spec: dict[str, Any],
                        label: str) -> list[H.Receptor]:
    """정면도 정의 1건으로 교사동 1개동의 수광점을 만든다."""
    segs = facade_segments(row["geom"], spec["facade_azimuth"],
                           spec.get("tol_deg", 30.0))
    if not segs:
        return []
    spacing = spec.get("spacing_m", 2.5)
    fh = spec["floor_height_m"]
    sill, head = spec["window_sill_m"], spec["window_head_m"]
    z_mid = (sill + head) / 2.0
    excl = [tuple(e) for e in spec.get("exclude_spans", [])]

    ux, uy, s0, _total = facade_frame(row, spec)
    out: list[H.Receptor] = []
    for (x0, y0), (x1, y1) in segs:
        seg_len = math.hypot(x1 - x0, y1 - y0)
        ex, ey = (x1 - x0) / seg_len, (y1 - y0) / seg_len
        nx, ny = ey, -ex
        polys = (row["geom"].geoms if row["geom"].geom_type == "MultiPolygon"
                 else [row["geom"]])
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        if any(p.contains(Point(mx + nx * 0.3, my + ny * 0.3)) for p in polys):
            nx, ny = -nx, -ny
        normal_az = math.degrees(math.atan2(nx, ny)) % 360

        k = 0
        while True:
            d = (k + 0.5) * spacing
            if d > seg_len:
                break
            k += 1
            px = x0 + ex * d + nx * 0.4
            py = y0 + ey * d + ny * 0.4
            # 전개 좌표는 구간 진행 방향과 무관하게 투영으로 정의한다
            s = (x0 + ex * d) * ux + (y0 + ey * d) * uy - s0
            if any(a <= s <= b for a, b in excl):
                continue
            for f in range(1, spec["n_floors"] + 1):
                out.append(H.Receptor(px, py, (f - 1) * fh + z_mid,
                                      normal_az, label, f))
    return out


def build_school_receptors(jibun: str, rows: Sequence[dict[str, Any]],
                           label_of, spec_all: dict[str, Any],
                           ) -> tuple[list[H.Receptor], bool]:
    """학교 1곳의 수광점. (수광점, 정면도정의_사용여부)를 돌려준다.

    label_of(row) 는 교사동 라벨을 만드는 콜백이다.
    """
    entry = spec_all.get(jibun)
    if not entry:
        return [], False
    by_floors = {b["match_floors"]: b for b in entry["buildings"]}
    out: list[H.Receptor] = []
    used = False
    for row in rows:
        spec = by_floors.get(row["floors"])
        label = label_of(row)
        if spec is None:
            out += H.make_receptors([{**row, "jibun": label}])
            continue
        rec = receptors_from_spec(row, spec, label)
        if rec:
            used = True
            out += rec
        else:
            out += H.make_receptors([{**row, "jibun": label}])
    return out, used


def facade_length(row: dict[str, Any], spec: dict[str, Any]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in facade_segments(row["geom"], spec["facade_azimuth"],
                                           spec.get("tol_deg", 30.0)))
