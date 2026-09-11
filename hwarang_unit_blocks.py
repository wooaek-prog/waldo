#!/usr/bin/env python3
"""화랑아파트 – 평형별 단위세대 평면 블럭 생성.

단위세대 평면도(A-101~A-107)의 치수열로부터 평형별 블럭 폴리곤을 만들어,
확정 기준층(36.97 × 18.48 m, 683.3㎡) 안에서 세대 조합을 탐색할 수 있는
입력 파일을 준비한다. 실제 조합 최적화는 이 블럭을 읽어 별도로 수행한다.

블럭 좌표계
    원점 = 전면(주향) 좌측 하단, +x = 전면 우측, +y = 깊이 방향, 단위 mm.
    front_edge(y=0)가 주향면이며 발코니는 이 변에 붙는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from shapely.affinity import rotate, scale, translate
from shapely.geometry import LineString, Polygon, box, mapping
from shapely.ops import unary_union

DEFAULT_CONFIG = Path(__file__).with_name("data") / "hwarang_unit_blocks.json"
DEFAULT_OUTDIR = Path(__file__).with_name("outputs") / "hwarang_units"


@dataclass
class UnitBlock:
    """평형별 단위세대 블럭."""

    code: str
    family: str
    exclusive_m2: float
    bays: list[dict[str, Any]]
    frame: Polygon                 # 골조 외곽(연면적 산정 대상)
    balcony: Polygon               # 발코니 포함 외형
    depth_scale: float
    tower_corner: bool
    note: str = ""
    sheet: str = ""

    @property
    def frontage_mm(self) -> float:
        return sum(b["w"] for b in self.bays)

    @property
    def max_depth_mm(self) -> float:
        return max(b["d"] for b in self.bays) * self.depth_scale

    @property
    def frame_m2(self) -> float:
        return self.frame.area / 1e6

    @property
    def balcony_m2(self) -> float:
        return (self.balcony.area - self.frame.area) / 1e6

    @property
    def efficiency(self) -> float:
        """전용면적 / 골조 점유면적."""
        return self.exclusive_m2 / self.frame_m2 if self.frame_m2 else 0.0


def bay_polygon(bays: Sequence[dict[str, Any]], depth_scale: float) -> Polygon:
    """베이별 폭·깊이로 계단형 직교 폴리곤을 만든다."""
    parts = []
    x = 0.0
    for bay in bays:
        parts.append(box(x, 0.0, x + bay["w"], bay["d"] * depth_scale))
        x += bay["w"]
    return unary_union(parts)


def apply_chamfer(poly: Polygon, chamfer: dict[str, Any] | None) -> Polygon:
    """모서리 세대의 45° 사선 컷을 적용한다."""
    if not chamfer:
        return poly
    minx, miny, maxx, maxy = poly.bounds
    dx, dy = float(chamfer["x_mm"]), float(chamfer["y_mm"])
    corner = chamfer.get("corner", "NW").upper()
    px, py = (minx, maxy) if corner == "NW" else (maxx, maxy)
    sign = 1.0 if corner == "NW" else -1.0
    cut = Polygon([(px, py), (px + sign * dx, py), (px, py - dy)])
    return poly.difference(cut)


def build_block(spec: dict[str, Any], frame_factor: float, balcony_mm: float,
                calibrate: bool) -> UnitBlock:
    raw = apply_chamfer(bay_polygon(spec["bays"], 1.0), spec.get("chamfer"))
    target = spec["exclusive_m2"] * frame_factor
    depth_scale = 1.0
    if calibrate and raw.area > 0:
        # 폭(베이 치수)은 도면 확정값이므로 깊이만 늘려 목표 골조면적에 맞춘다
        depth_scale = target / (raw.area / 1e6)
    frame = apply_chamfer(bay_polygon(spec["bays"], depth_scale), spec.get("chamfer"))
    # 발코니는 전면(y=0) 변에만 붙인다
    front = box(0.0, -balcony_mm, sum(b["w"] for b in spec["bays"]), 0.0)
    balcony = unary_union([frame, front])
    return UnitBlock(
        code=spec["code"], family=spec["family"],
        exclusive_m2=float(spec["exclusive_m2"]), bays=spec["bays"],
        frame=frame, balcony=balcony, depth_scale=depth_scale,
        tower_corner=bool(spec.get("tower_corner", False)),
        note=spec.get("note", ""), sheet=spec.get("sheet", ""),
    )


# --------------------------------------------------------------------------- #
# 기준층 조합 가능성
# --------------------------------------------------------------------------- #
def plate_capacity(plate: dict[str, Any], core: dict[str, Any],
                   blocks: Sequence[UnitBlock]) -> list[dict[str, Any]]:
    """평형별 기준층 수용 가능 세대수와 그때 남는 코어 대역 폭을 계산한다.

    기준층 단변(18.48 m)은 '세대 깊이 × 2 + 중앙 코어 대역'으로 쓰인다.
    코어가 중앙 띠형이면 두 장변 파사드가 그대로 남으므로 전면길이를 잠식하지 않는다.
    """
    long_m = plate["long_mm"] / 1000.0
    short_m = plate["short_mm"] / 1000.0
    core_m2 = core["width_mm"] * core["depth_mm"] / 1e6
    core_depth_m = core["depth_mm"] / 1000.0
    band_core = core_depth_m <= short_m / 3.0     # 중앙 띠형 코어
    usable_m2 = plate["area_m2"] - core_m2
    rows = []
    for block in blocks:
        depth_m = block.max_depth_mm / 1000.0
        band_m = short_m - depth_m * 2            # 2열 배치 시 남는 중앙 대역
        if band_m >= 0.0:
            # 띠형 코어는 파사드를 잠식하지 않는다
            frontage_avail = long_m * 2 if band_core else (long_m - core["width_mm"] / 1000.0) * 2
            placement = "장변 2열"
        elif depth_m <= long_m:
            # 깊이가 단변을 넘으면 90° 돌려 단변부(플레이트 양 끝)에 세운다
            frontage_avail = short_m * 2
            placement = "단변부 90° 회전"
        else:
            frontage_avail = 0.0
            placement = "수용 불가"
        by_frontage = math.floor(frontage_avail / (block.frontage_mm / 1000.0))
        by_area = math.floor(usable_m2 / block.frame_m2)
        rows.append({
            "code": block.code,
            "frontage_m": block.frontage_mm / 1000.0,
            "depth_m": depth_m,
            "frame_m2": block.frame_m2,
            "core_band_m": band_m,
            "core_band_ok": band_m >= core_depth_m,
            "units_by_frontage": by_frontage,
            "units_by_area": by_area,
            "units": min(by_frontage, by_area),
            "placement": placement,
        })
    return rows


def place_on_plate(block: UnitBlock, plate: dict[str, Any], x_mm: float,
                   y_mm: float, rotation_deg: float = 0.0,
                   mirror: bool = False) -> Polygon:
    """블럭을 기준층 로컬좌표(장변 x, 단변 y)에 배치한다."""
    geom = scale(block.frame, -1.0, 1.0, origin="centroid") if mirror else block.frame
    geom = rotate(geom, rotation_deg, origin=(0.0, 0.0))
    return translate(geom, x_mm, y_mm)


def plate_to_world(geom: Polygon, plate: dict[str, Any]) -> Polygon:
    """기준층 로컬좌표(mm) → EPSG:5186 실좌표(m)."""
    metres = scale(geom, 0.001, 0.001, origin=(0.0, 0.0))
    centred = translate(metres, -plate["long_mm"] / 2000.0, -plate["short_mm"] / 2000.0)
    # 장변(로컬 +x)을 장변 방위각에 맞춘다: 방위 az ↔ 수학각 (90 - az)
    turned = rotate(centred, 90.0 - plate["long_azimuth_deg"], origin=(0.0, 0.0))
    cx, cy = plate["centre_epsg5186"]
    return translate(turned, cx, cy)


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
def write_geojson(path: Path, features: Sequence[tuple[Polygon, dict[str, Any]]],
                  epsg: int | None) -> None:
    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": mapping(g), "properties": p}
                     for g, p in features],
    }
    if epsg:
        payload["crs"] = {"type": "name",
                          "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def export_block_sheet(path: Path, blocks: Sequence[UnitBlock],
                       balcony_mm: float) -> Path:
    """평형별 블럭을 한 장에 나란히 그린 검토용 시트."""
    cols, cell, pad = 4, 300.0, 18.0
    rows_n = (len(blocks) + cols - 1) // cols
    width, height = cols * cell, rows_n * cell + 46
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
           f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}">',
           '<rect width="100%" height="100%" fill="#fbfbf9"/>',
           '<text x="14" y="27" font-family="sans-serif" font-size="17" '
           'font-weight="bold" fill="#111">화랑아파트 평형별 단위세대 블럭 '
           f'(발코니 {balcony_mm:.0f}mm, 상단이 배면)</text>']
    for i, block in enumerate(blocks):
        ox, oy = (i % cols) * cell, 46 + (i // cols) * cell
        minx, miny, maxx, maxy = block.balcony.bounds
        span = max(maxx - minx, maxy - miny) or 1.0
        k = (cell - 2 * pad - 52) / span
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2

        def svg_path(geom, ox=ox, oy=oy, k=k, cx=cx, cy=cy) -> str:
            polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
            return "".join(
                '<polygon points="%s" />' % " ".join(
                    f"{ox + cell / 2 + (x - cx) * k:.1f},"
                    f"{oy + cell / 2 + 14 - (y - cy) * k:.1f}"
                    for x, y in p.exterior.coords)
                for p in polys)

        out.append(f'<rect x="{ox + 4}" y="{oy + 2}" width="{cell - 8}" '
                   f'height="{cell - 8}" fill="#fff" stroke="#ddd"/>')
        out.append(f'<g fill="#8ecae6" fill-opacity="0.55" stroke="#457b9d" '
                   f'stroke-width="1">{svg_path(block.balcony)}</g>')
        out.append(f'<g fill="#1d3557" fill-opacity="0.88" stroke="#0b1c30" '
                   f'stroke-width="1.5">{svg_path(block.frame)}</g>')
        # 베이 구획선
        x = 0.0
        for bay in block.bays[:-1]:
            x += bay["w"]
            line = LineString([(x, 0), (x, bay["d"] * block.depth_scale)])
            pts = " ".join(f"{ox + cell / 2 + (px - cx) * k:.1f},"
                           f"{oy + cell / 2 + 14 - (py - cy) * k:.1f}"
                           for px, py in line.coords)
            out.append(f'<polyline points="{pts}" fill="none" stroke="#ffffff" '
                       f'stroke-width="0.9" stroke-dasharray="3 2"/>')
        out.append(f'<text x="{ox + 14}" y="{oy + 26}" font-family="sans-serif" '
                   f'font-size="13.5" font-weight="bold" fill="#111">'
                   f'{block.code} · 전용 {block.exclusive_m2:.0f}㎡</text>')
        out.append(f'<text x="{ox + 14}" y="{oy + cell - 32}" font-family="sans-serif" '
                   f'font-size="11.5" fill="#333">전면 {block.frontage_mm / 1000:.2f} m · '
                   f'깊이 {block.max_depth_mm / 1000:.2f} m</text>')
        out.append(f'<text x="{ox + 14}" y="{oy + cell - 17}" font-family="sans-serif" '
                   f'font-size="11.5" fill="#555">골조 {block.frame_m2:.1f}㎡ · '
                   f'발코니 {block.balcony_m2:.1f}㎡ · {block.family}</text>')
    out.append("</svg>")
    path.write_text("\n".join(out), encoding="utf-8")
    return path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="화랑아파트 평형별 단위세대 블럭 생성")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--balcony", type=float, default=None,
                        help="발코니 깊이(mm). 미지정 시 설정파일 값")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="깊이 보정 없이 도면 판독값 그대로 사용")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    args.outdir.mkdir(parents=True, exist_ok=True)

    balcony_mm = args.balcony if args.balcony is not None else config["balcony_depth_mm"]
    calibrate = config.get("calibrate", True) and not args.no_calibrate
    frame_factor = config["frame_factor"]
    plate, core = config["plate"], config["core"]

    blocks = [build_block(spec, frame_factor, balcony_mm, calibrate)
              for spec in config["units"]]

    print(f"기준층 {plate['long_mm'] / 1000:.2f} × {plate['short_mm'] / 1000:.2f} m "
          f"= {plate['area_m2']:,.1f}㎡ (장변 방위 {plate['long_azimuth_deg']:g}°)")
    print(f"코어 {core['width_mm'] / 1000:.1f} × {core['depth_mm'] / 1000:.1f} m "
          f"= {core['width_mm'] * core['depth_mm'] / 1e6:,.1f}㎡ · "
          f"세대 가용 {plate['area_m2'] - core['width_mm'] * core['depth_mm'] / 1e6:,.1f}㎡")
    print(f"발코니 {balcony_mm:.0f} mm (전면 1면, 연면적 제외) · "
          f"골조계수 {frame_factor:g}{' · 깊이 보정 적용' if calibrate else ''}\n")

    print(f"{'평형':<7}{'계열':<18}{'전용㎡':>7}{'전면m':>8}{'깊이m':>7}"
          f"{'골조㎡':>8}{'발코니㎡':>9}{'효율':>7}{'깊이보정':>9}")
    print("-" * 82)
    for block in blocks:
        print(f"{block.code:<7}{block.family:<18}{block.exclusive_m2:>7.0f}"
              f"{block.frontage_mm / 1000:>8.2f}{block.max_depth_mm / 1000:>7.2f}"
              f"{block.frame_m2:>8.1f}{block.balcony_m2:>9.1f}"
              f"{block.efficiency * 100:>6.1f}%{block.depth_scale:>9.3f}")

    short_m = plate["short_mm"] / 1000.0
    core_depth_m = core["depth_mm"] / 1000.0
    print(f"\n배치 제약: 단변 {short_m:.2f} m = 세대 깊이 × 2 + 중앙 코어 대역")
    print(f"   코어 대역 {core_depth_m:.2f} m 확보 시 세대 깊이 상한 "
          f"{(short_m - core_depth_m) / 2:.2f} m")
    print(f"\n기준층 수용 가능 세대수")
    print(f"{'평형':<7}{'전면m':>8}{'깊이m':>7}{'코어대역m':>10}{'배치방식':<16}"
          f"{'전면기준':>9}{'면적기준':>9}{'수용':>6}")
    print("-" * 78)
    caps = plate_capacity(plate, core, blocks)
    for cap in caps:
        band = f"{cap['core_band_m']:.2f}" if cap["core_band_m"] >= 0 else "―"
        mark = "" if cap["core_band_ok"] or cap["core_band_m"] < 0 else " ▲"
        print(f"{cap['code']:<7}{cap['frontage_m']:>8.2f}{cap['depth_m']:>7.2f}"
              f"{band:>9}{mark:<2}{cap['placement']:<14}"
              f"{cap['units_by_frontage']:>9}{cap['units_by_area']:>9}{cap['units']:>6}")
    print("   ▲ = 2열 배치 시 코어 대역이 부족해 세대 깊이를 줄여야 하는 평형")

    write_outputs(args, config, blocks, caps, balcony_mm)
    return 0


def write_outputs(args, config, blocks, caps, balcony_mm) -> None:
    plate, core = config["plate"], config["core"]

    # 1) 블럭 로컬좌표(mm) – 조합 최적화 입력용
    features: list[tuple[Polygon, dict[str, Any]]] = []
    for block in blocks:
        common = {
            "code": block.code, "family": block.family,
            "exclusive_m2": block.exclusive_m2,
            "frame_m2": round(block.frame_m2, 2),
            "balcony_m2": round(block.balcony_m2, 2),
            "frontage_mm": block.frontage_mm,
            "depth_mm": round(block.max_depth_mm, 1),
            "efficiency": round(block.efficiency, 3),
            "tower_corner": block.tower_corner,
            "sheet": block.sheet,
        }
        features.append((block.frame, dict(common, line="골조 외곽")))
        features.append((block.balcony, dict(common, line="발코니 포함 외형")))
    write_geojson(args.outdir / "hwarang_unit_blocks_local_mm.geojson", features, None)

    # 2) 기준층 + 코어 (로컬 mm, 조합 캔버스)
    plate_poly = box(0, 0, plate["long_mm"], plate["short_mm"])
    core_poly = box((plate["long_mm"] - core["width_mm"]) / 2,
                    (plate["short_mm"] - core["depth_mm"]) / 2,
                    (plate["long_mm"] + core["width_mm"]) / 2,
                    (plate["short_mm"] + core["depth_mm"]) / 2)
    write_geojson(args.outdir / "hwarang_plate_canvas_local_mm.geojson", [
        (plate_poly, {"line": "기준층 연면적선", "area_m2": plate["area_m2"],
                      "long_mm": plate["long_mm"], "short_mm": plate["short_mm"]}),
        (core_poly, {"line": "코어(가정)", "area_m2": round(core_poly.area / 1e6, 1)}),
    ], None)

    # 3) 실좌표 배치 캔버스 (EPSG:5186) – 배치도와 겹쳐 보기용
    write_geojson(args.outdir / "hwarang_plate_canvas_epsg5186.geojson", [
        (plate_to_world(plate_poly, plate),
         {"line": "기준층 연면적선", "area_m2": plate["area_m2"]}),
        (plate_to_world(core_poly, plate),
         {"line": "코어(가정)", "area_m2": round(core_poly.area / 1e6, 1)}),
    ], 5186)

    # 4) 제원표
    with (args.outdir / "hwarang_unit_blocks.csv").open(
            "w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(["평형", "도면", "계열", "전용㎡", "전면m", "최대깊이m",
                         "골조㎡", "발코니㎡", "전용/골조", "깊이보정계수",
                         "코너세대", "베이수", "베이폭mm", "코어대역m", "배치방식",
                         "전면기준세대", "면적기준세대", "수용세대", "비고"])
        cap_by_code = {c["code"]: c for c in caps}
        for block in blocks:
            cap = cap_by_code[block.code]
            writer.writerow([
                block.code, block.sheet, block.family, block.exclusive_m2,
                round(block.frontage_mm / 1000, 2), round(block.max_depth_mm / 1000, 2),
                round(block.frame_m2, 1), round(block.balcony_m2, 1),
                round(block.efficiency, 3), round(block.depth_scale, 3),
                "O" if block.tower_corner else "", len(block.bays),
                "/".join(str(b["w"]) for b in block.bays),
                round(cap["core_band_m"], 2), cap["placement"],
                cap["units_by_frontage"], cap["units_by_area"], cap["units"],
                block.note,
            ])

    export_block_sheet(args.outdir / "hwarang_unit_blocks_sheet.svg", blocks, balcony_mm)
    print(f"\n결과 저장: {args.outdir}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0) from None
