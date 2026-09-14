#!/usr/bin/env python3
"""학교 교사동의 '실제 창면' 수광점 생성.

기존 일반식(hwarang_massing_study.make_receptors)은 외벽 전체를 5m 간격으로
쪼개고 남향계열(방위 90~270°) 벽면이면 모두 수광점을 두었다. 그래서
    · 교실이 없는 **단부벽·계단실**에도 수광점이 생기고
    · 층고 3.5m·창중심 1.2m 를 모든 학교에 똑같이 적용
하는 한계가 있었다.

여기서는 정면도에서 읽은 값(data/school_facades.json)으로
    · **지정한 파사드에만** 수광점을 두고(단일 방위 ±tol, 또는 방위 구간)
    · 층별 바닥레벨·창 하단/상단을 건물별로 적용해 **창 중앙 높이**를 쓰고
    · 계단실·출입구 등 교실창이 없는 구간을 (층을 지정해) 뺀다.
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


# ---------------------------------------------------------------- 건물 매칭

def matches(row: dict[str, Any], spec: dict[str, Any]) -> bool:
    """정면도 정의 1건이 이 AL_D010 행을 가리키는가.

    구식 정의는 `match_floors`(층수)만 쓴다. 신식 정의는 `match` 안에
    floors / area_m2(+area_tol) / near([x,y] + near_tol) 를 조합한다.
    층수·높이가 비어 있는 행(AL_D010 A9/A26 결측)도 면적·위치로 집을 수 있다.
    """
    if "match_floors" in spec and "match" not in spec:
        return row["floors"] == spec["match_floors"]
    m = spec.get("match", {})
    if "floors" in m and row["floors"] != m["floors"]:
        return False
    if "area_m2" in m:
        if abs(row["geom"].area - m["area_m2"]) > m.get("area_tol", 2.0):
            return False
    if "near" in m:
        c = row["geom"].centroid
        if math.hypot(c.x - m["near"][0], c.y - m["near"][1]) > m.get("near_tol", 5.0):
            return False
    return bool(m)


def find_row(rows: Sequence[dict[str, Any]], spec: dict[str, Any]):
    for row in rows:
        if matches(row, spec):
            return row
    return None


# ---------------------------------------------------------------- 층·창 높이

def augment_rows(jibun: str, rows: Sequence[dict[str, Any]],
                 buildings: Sequence[dict[str, Any]], entry: dict[str, Any],
                 ) -> list[dict[str, Any]]:
    """정면도가 높이를 주는 건물은 AL_D010 결측이어도 살려 넣는다.

    school_parcel_rows() 는 층수·높이가 모두 비어 복원이 불가능한 행을 버린다.
    그런 동이라도 사용자가 준 도면에 높이가 있으면 여기서 되살린다
    (예: 여의도중 체육관동 부속 787.5㎡ – A9/A16/A26 전부 결측).
    """
    out = list(rows)
    for spec in entry.get("buildings", []):
        if not spec.get("height_m") or find_row(out, spec) is not None:
            continue
        for b in buildings:
            if b.get("jibun") != jibun:
                continue
            cand = {**b, "floors": len(floor_levels(spec)),
                    "height_m": float(spec["height_m"]),
                    "classroom": True, "height_source": "facade_spec"}
            if matches(cand, spec):
                out.append(cand)
                break
    return out


def floor_levels(spec: dict[str, Any]) -> list[float]:
    """각 층 바닥레벨(1층 바닥 = 0). 정면도에 층별 치수가 있으면 그대로 쓴다."""
    if spec.get("floor_levels_m"):
        return [float(z) for z in spec["floor_levels_m"]]
    fh = spec["floor_height_m"]
    return [(f - 1) * fh for f in range(1, spec["n_floors"] + 1)]


def building_top(row: dict[str, Any], spec: dict[str, Any]) -> float:
    """입면 도면의 높이. AL_D010 높이가 비어 있으면 정면도 값을 쓴다."""
    if spec.get("height_m"):
        return float(spec["height_m"])
    return float(row.get("height_m") or 0.0)


# ---------------------------------------------------------------- 파사드 정의

def spec_facades(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """건물 1개동의 파사드 목록. 구식(단일 facade_azimuth)도 그대로 받는다."""
    if spec.get("facades"):
        return spec["facades"]
    return [{"azimuth": spec["facade_azimuth"],
             "tol_deg": spec.get("tol_deg", 30.0),
             "exclude_spans": spec.get("exclude_spans", [])}]


def facade_id(spec: dict[str, Any], facade: dict[str, Any], idx: int) -> str:
    return facade.get("id") or (spec["id"] if idx == 0 else f"{spec['id']}-{idx+1}")


def _wall_azimuth(poly, x0, y0, x1, y1) -> float | None:
    """벽면 바깥 법선 방위. 너무 짧은 변은 None."""
    ex, ey = x1 - x0, y1 - y0
    n = math.hypot(ex, ey)
    if n < 0.2:
        return None
    nx, ny = ey / n, -ex / n
    if poly.contains(Point((x0 + x1) / 2 + nx * 0.3, (y0 + y1) / 2 + ny * 0.3)):
        nx, ny = -nx, -ny
    return math.degrees(math.atan2(nx, ny)) % 360


def _in_facade(az: float, facade: dict[str, Any]) -> bool:
    rng = facade.get("azimuth_range")
    if rng:
        lo, hi = rng
        return (az - lo) % 360 <= (hi - lo) % 360
    return abs((az - facade["azimuth"] + 180) % 360 - 180) <= facade.get("tol_deg", 30.0)


def facade_azimuth_ref(facade: dict[str, Any]) -> float:
    """전개축 기준 방위(구간 정의면 구간 중앙)."""
    if facade.get("azimuth_range"):
        lo, hi = facade["azimuth_range"]
        return (lo + ((hi - lo) % 360) / 2.0) % 360
    return facade["azimuth"]


def facade_segments(geom, facade: dict[str, Any]
                    ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """이 파사드에 속하는 외벽 구간. 전개 순서(0m→끝)대로 돌려준다."""
    polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
    out: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for poly in polys:
        coords = list(poly.exterior.coords)
        picked = []
        for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
            az = _wall_azimuth(poly, x0, y0, x1, y1)
            if az is not None and _in_facade(az, facade):
                picked.append(((x0, y0), (x1, y1)))
        out += picked

    if not out:
        return out

    ang = math.radians(facade_azimuth_ref(facade) - 90.0)
    ux, uy = math.sin(ang), math.cos(ang)
    if facade.get("azimuth_range"):
        # 꺾인 외벽을 링 순서대로 이어 전개한다. 진행 방향만 통일한다.
        net = ((out[-1][1][0] - out[0][0][0]) * ux
               + (out[-1][1][1] - out[0][0][1]) * uy)
        if net < 0:
            out = [(b, a) for a, b in reversed(out)]
        return out
    # 한 평면 파사드: 전개축 투영으로 정렬한다(구간 저장 순서와 무관).
    out.sort(key=lambda s: (s[0][0] + s[1][0]) / 2 * ux
             + (s[0][1] + s[1][1]) / 2 * uy)
    return out


def facade_frame(row: dict[str, Any], facade: dict[str, Any]):
    """파사드 전개 좌표계.

    한 평면 파사드  : 반환 ("proj", ux, uy, s0, total)
                      s = (x*ux + y*uy) − s0
    꺾인 파사드     : 반환 ("arc", segs, total)
                      s = 앞 구간 길이 누적 + 해당 구간 내 거리
    두 경우 모두 구간의 저장 순서·방향과 무관하게 같은 s 를 준다.
    """
    segs = facade_segments(row["geom"], facade)
    if not segs:
        return ("proj", 0.0, 0.0, 0.0, 0.0)
    if facade.get("azimuth_range"):
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs)
        return ("arc", segs, total)
    ang = math.radians(facade_azimuth_ref(facade) - 90.0)
    ux, uy = math.sin(ang), math.cos(ang)
    proj = [p[0] * ux + p[1] * uy for seg in segs for p in seg]
    return ("proj", ux, uy, min(proj), max(proj) - min(proj))


def frame_distance(frame, x: float, y: float) -> float:
    """전개 좌표계 위의 거리 s."""
    if frame[0] == "proj":
        _, ux, uy, s0, _ = frame
        return x * ux + y * uy - s0
    _, segs, _ = frame
    run = 0.0
    best, best_d = 0.0, float("inf")
    for (x0, y0), (x1, y1) in segs:
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len < 1e-9:
            continue
        t = (((x - x0) * (x1 - x0) + (y - y0) * (y1 - y0)) / (seg_len ** 2))
        tc = min(1.0, max(0.0, t))
        px, py = x0 + (x1 - x0) * tc, y0 + (y1 - y0) * tc
        d = math.hypot(x - px, y - py)
        if d < best_d:
            best_d, best = d, run + seg_len * tc
        run += seg_len
    return best


def frame_total(frame) -> float:
    return frame[4] if frame[0] == "proj" else frame[2]


# ---------------------------------------------------------------- 제외 구간

def excluded_spans(facade: dict[str, Any]) -> list[tuple[float, float, set[int] | None]]:
    """[시작, 끝] 또는 {"span":[시작,끝], "floors":[층…]} 을 통일해 돌려준다.

    floors 가 없으면 전 층에서 제외한다(계단실 등). 층을 주면 그 층만
    제외한다(1층 출입구 위로 교실창이 이어지는 경우).
    """
    out = []
    for e in facade.get("exclude_spans", []):
        if isinstance(e, dict):
            a, b = e["span"]
            fl = set(e["floors"]) if e.get("floors") else None
        else:
            a, b = e
            fl = None
        out.append((float(a), float(b), fl))
    return out


# ---------------------------------------------------------------- 수광점 생성

def receptors_from_facade(row: dict[str, Any], spec: dict[str, Any],
                          facade: dict[str, Any], label: str) -> list[H.Receptor]:
    """정면도 정의 1건으로 파사드 1면의 수광점을 만든다."""
    segs = facade_segments(row["geom"], facade)
    if not segs:
        return []
    spacing = spec.get("spacing_m", 2.5)
    sill, head = spec["window_sill_m"], spec["window_head_m"]
    z_mid = (sill + head) / 2.0
    levels = floor_levels(spec)
    excl = excluded_spans(facade)
    frame = facade_frame(row, facade)

    polys = (row["geom"].geoms if row["geom"].geom_type == "MultiPolygon"
             else [row["geom"]])
    out: list[H.Receptor] = []
    for (x0, y0), (x1, y1) in segs:
        seg_len = math.hypot(x1 - x0, y1 - y0)
        ex, ey = (x1 - x0) / seg_len, (y1 - y0) / seg_len
        nx, ny = ey, -ex
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
            wx, wy = x0 + ex * d, y0 + ey * d
            s = frame_distance(frame, wx, wy)
            px, py = wx + nx * 0.4, wy + ny * 0.4
            for f, z0 in enumerate(levels, start=1):
                if any(a <= s <= b and (fl is None or f in fl) for a, b, fl in excl):
                    continue
                out.append(H.Receptor(px, py, z0 + z_mid, normal_az, label, f))
    return out


def receptors_for_building(row: dict[str, Any], spec: dict[str, Any]
                           ) -> list[H.Receptor]:
    out: list[H.Receptor] = []
    for i, facade in enumerate(spec_facades(spec)):
        out += receptors_from_facade(row, spec, facade, facade_id(spec, facade, i))
    return out


def receptors_from_spec(row: dict[str, Any], spec: dict[str, Any],
                        label: str) -> list[H.Receptor]:
    """구식 호출부 호환 – 라벨을 강제로 하나로 준다."""
    out: list[H.Receptor] = []
    for facade in spec_facades(spec):
        out += receptors_from_facade(row, spec, facade, label)
    return out


def build_school_receptors(jibun: str, rows: Sequence[dict[str, Any]],
                           label_of, spec_all: dict[str, Any],
                           ) -> tuple[list[H.Receptor], bool]:
    """학교 1곳의 수광점. (수광점, 정면도정의_사용여부)를 돌려준다.

    label_of(row) 는 정면도 정의가 없는 동의 라벨을 만드는 콜백이다.
    """
    entry = spec_all.get(jibun)
    if not entry:
        return [], False
    out: list[H.Receptor] = []
    used = False
    claimed: set[int] = set()
    for spec in entry["buildings"]:
        row = find_row(rows, spec)
        if row is None:
            continue
        rec = receptors_for_building(row, spec)
        if rec:
            used = True
            out += rec
            claimed.add(id(row))
    for row in rows:
        if id(row) in claimed:
            continue
        out += H.make_receptors([{**row, "jibun": label_of(row)}])
    return out, used


def facade_length(row: dict[str, Any], facade: dict[str, Any]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1])
               for a, b in facade_segments(row["geom"], facade))
