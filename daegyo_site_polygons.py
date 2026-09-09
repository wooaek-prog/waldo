#!/usr/bin/env python3
"""여의도 대교아파트 재건축 배치도 → QGIS 일조권 분석용 건물 폴리곤 생성 도구.

첨부 단지배치도(래스터)에서 트레이싱한 픽셀좌표 폴리곤을 읽어
  1) 인가 대지면적(26,869.5㎡)에 맞추어 축척을 자동 산정하고
  2) 방위각/앵커 또는 기준점 2점 상사변환으로 실좌표계(기본 EPSG:5186)로 변환한 뒤
  3) 층수 기반 높이 속성을 부여한 GeoJSON/CSV를 출력합니다.

출력 레이어는 QGIS 3D 맵뷰의 그림자(태양광) 분석, Qgis2threejs, UMEP 등
일조권 검토 도구에 그대로 사용할 수 있습니다.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from pyproj import CRS, Transformer
except ImportError as exc:  # pragma: no cover - 의존성 안내
    raise SystemExit("pyproj가 필요합니다. `pip install pyproj shapely` 후 다시 실행하세요.") from exc

try:
    from shapely.geometry import Polygon, mapping
    from shapely.ops import unary_union
except ImportError as exc:  # pragma: no cover - 의존성 안내
    raise SystemExit("shapely가 필요합니다. `pip install pyproj shapely` 후 다시 실행하세요.") from exc


DEFAULT_CONFIG = Path(__file__).with_name("data") / "daegyo_site_plan.json"
DEFAULT_OUTDIR = Path(__file__).with_name("outputs") / "daegyo"


@dataclass(frozen=True)
class PlanTransform:
    """픽셀좌표 → 실좌표 상사변환(축척·회전·평행이동).

    복소수 계수 ``a``, ``t``에 대해 ``E + iN = a * (px - i*py) + t`` 로 정의한다.
    (픽셀 y축은 아래로 증가하므로 허수부에 음수를 취해 상향 좌표계로 바꾼다.)
    """

    a: complex
    t: complex
    epsg: int

    @property
    def scale_m_per_px(self) -> float:
        return abs(self.a)

    @property
    def plan_up_azimuth_deg(self) -> float:
        """배치도 도면상 '위쪽' 방향의 진북 기준 방위각(도)."""
        return (-math.degrees(math.atan2(self.a.imag, self.a.real))) % 360.0

    def apply(self, px: float, py: float) -> tuple[float, float]:
        z = complex(px, -py)
        w = self.a * z + self.t
        return w.real, w.imag


@dataclass
class Feature:
    """출력 대상 건물 한 동(또는 존)."""

    fid: str
    layer: str
    polygon_px: Polygon
    attributes: dict[str, Any] = field(default_factory=dict)
    polygon_map: Polygon | None = None


# --------------------------------------------------------------------------- #
# 입력 처리
# --------------------------------------------------------------------------- #
def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="여의도 대교아파트 배치도 기반 QGIS 건물 폴리곤 생성",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"배치도 트레이싱 정의 JSON (기본: {DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help=f"출력 디렉터리 (기본: {DEFAULT_OUTDIR})",
    )
    parser.add_argument("--epsg", type=int, default=None, help="출력 투영좌표계 EPSG (기본: 5186)")
    parser.add_argument(
        "--plan-up-azimuth",
        type=float,
        default=None,
        help="배치도 위쪽 방향의 진북 기준 방위각(도). 기본값은 설정파일 값(35도).",
    )
    parser.add_argument("--anchor-lat", type=float, default=None, help="대지 중심 위도(WGS84)")
    parser.add_argument("--anchor-lon", type=float, default=None, help="대지 중심 경도(WGS84)")
    parser.add_argument(
        "--control-points",
        type=str,
        default=None,
        help=(
            "기준점 2점으로 정밀 보정. 형식: 'px1,py1,E1,N1;px2,py2,E2,N2' "
            "(px,py=배치도 픽셀좌표, E,N=--epsg 좌표계의 실좌표). "
            "지정 시 축척·회전·위치를 모두 기준점에서 산출한다."
        ),
    )
    parser.add_argument(
        "--no-context",
        action="store_true",
        help="주변 기존 건물(삼부·장미·화랑·한양·여의도여고) 참고 레이어를 생성하지 않음",
    )
    return parser.parse_args(argv)


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fp:
        return json.load(fp)


def parse_control_points(raw: str) -> list[tuple[float, float, float, float]]:
    points: list[tuple[float, float, float, float]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        values = [float(v) for v in chunk.split(",")]
        if len(values) != 4:
            raise ValueError(f"기준점 형식 오류: '{chunk}' (px,py,E,N 4개 값이 필요)")
        points.append((values[0], values[1], values[2], values[3]))
    if len(points) != 2:
        raise ValueError("기준점은 정확히 2점을 지정해야 합니다.")
    return points


# --------------------------------------------------------------------------- #
# 기하 생성
# --------------------------------------------------------------------------- #
def polygon_from_spec(spec: dict[str, Any]) -> Polygon:
    """설정 항목의 rect_px 또는 ring_px 로부터 픽셀좌표 폴리곤을 만든다."""
    if "ring_px" in spec:
        return Polygon(spec["ring_px"])
    x0, y0, x1, y1 = spec["rect_px"]
    return Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def transform_polygon(polygon: Polygon, transform: PlanTransform) -> Polygon:
    return Polygon([transform.apply(x, y) for x, y in polygon.exterior.coords])


def build_transform(config: dict[str, Any], args: argparse.Namespace) -> tuple[PlanTransform, float]:
    """상사변환과 대지면적 기준 축척(m/px)을 계산한다."""
    geo = config["georeference"]
    epsg = args.epsg or int(geo.get("target_epsg", 5186))

    site_px = Polygon(config["site_boundary_px"])
    site_area_px = site_px.area
    approved_area = float(config["approval"]["site_area_m2"])
    area_scale = math.sqrt(approved_area / site_area_px)

    if args.control_points:
        (px1, py1, e1, n1), (px2, py2, e2, n2) = parse_control_points(args.control_points)
        z1, z2 = complex(px1, -py1), complex(px2, -py2)
        w1, w2 = complex(e1, n1), complex(e2, n2)
        if z1 == z2:
            raise ValueError("기준점 2점의 픽셀좌표가 동일합니다.")
        a = (w2 - w1) / (z2 - z1)
        t = w1 - a * z1
        return PlanTransform(a=a, t=t, epsg=epsg), area_scale

    azimuth = args.plan_up_azimuth
    if azimuth is None:
        azimuth = float(geo.get("plan_up_azimuth_deg", 35.0))
    lat = args.anchor_lat if args.anchor_lat is not None else float(geo["anchor_lat"])
    lon = args.anchor_lon if args.anchor_lon is not None else float(geo["anchor_lon"])

    to_map = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    anchor_e, anchor_n = to_map.transform(lon, lat)

    a = area_scale * complex(math.cos(math.radians(azimuth)), -math.sin(math.radians(azimuth)))
    centroid = site_px.centroid
    z_c = complex(centroid.x, -centroid.y)
    t = complex(anchor_e, anchor_n) - a * z_c
    return PlanTransform(a=a, t=t, epsg=epsg), area_scale


def make_features(config: dict[str, Any], include_context: bool) -> list[Feature]:
    levels = config["levels"]
    ground = float(levels["planned_ground_elev_m"])
    res_h = float(levels["residential_floor_height_m"])
    podium_h = float(levels["podium_floor_height_m"])
    roof_h = float(levels["rooftop_structure_m"])

    features: list[Feature] = []
    for spec in config["buildings"]:
        is_podium = bool(spec.get("podium", False))
        floors = int(spec["floors"])
        floor_height = podium_h if is_podium else res_h
        rooftop = 0.0 if is_podium else roof_h
        height = floors * floor_height + rooftop
        features.append(
            Feature(
                fid=spec["id"],
                layer="podium" if is_podium else "tower",
                polygon_px=polygon_from_spec(spec),
                attributes={
                    "id": spec["id"],
                    "dong": spec.get("dong", ""),
                    "part": spec.get("part", ""),
                    "use": spec.get("use", ""),
                    "floors": floors,
                    "floor_h_m": floor_height,
                    "roof_m": rooftop,
                    "height_m": round(height, 2),
                    "base_elev_m": ground,
                    "top_elev_m": round(ground + height, 2),
                    "source": "배치도 트레이싱(개략)",
                },
            )
        )

    if include_context:
        for spec in config.get("context_buildings", []):
            floors = int(spec["floors"])
            height = floors * res_h + roof_h
            features.append(
                Feature(
                    fid=spec["id"],
                    layer="context",
                    polygon_px=polygon_from_spec(spec),
                    attributes={
                        "id": spec["id"],
                        "dong": spec.get("name", ""),
                        "part": "주변 참고 건물",
                        "use": "참고(주변현황)",
                        "floors": floors,
                        "floor_h_m": res_h,
                        "roof_m": roof_h,
                        "height_m": round(height, 2),
                        "base_elev_m": ground,
                        "top_elev_m": round(ground + height, 2),
                        "source": "배치도 참고표기(개략) - 실측 대체 필요",
                    },
                )
            )
    return features


# --------------------------------------------------------------------------- #
# 검증
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Validation:
    site_area_m2: float
    building_area_m2: float
    coverage_pct: float
    gfa_m2: float
    far_pct: float
    approved_coverage_pct: float
    approved_far_pct: float

    @property
    def coverage_dev_pct(self) -> float:
        return (self.coverage_pct - self.approved_coverage_pct) / self.approved_coverage_pct * 100.0

    @property
    def far_dev_pct(self) -> float:
        return (self.far_pct - self.approved_far_pct) / self.approved_far_pct * 100.0


def validate(config: dict[str, Any], features: Iterable[Feature], site_map: Polygon) -> Validation:
    onsite = [f for f in features if f.layer in {"tower", "podium"} and f.polygon_map is not None]
    footprints = [f.polygon_map for f in onsite]
    building_area = unary_union(footprints).intersection(site_map).area if footprints else 0.0
    gfa = sum(f.polygon_map.area * int(f.attributes["floors"]) for f in onsite)
    site_area = site_map.area
    approval = config["approval"]
    return Validation(
        site_area_m2=site_area,
        building_area_m2=building_area,
        coverage_pct=building_area / site_area * 100.0,
        gfa_m2=gfa,
        far_pct=gfa / site_area * 100.0,
        approved_coverage_pct=float(approval["building_coverage_ratio_pct"]),
        approved_far_pct=float(approval["floor_area_ratio_pct"]),
    )


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
def geojson_dump(
    path: Path,
    features: Sequence[tuple[Polygon, dict[str, Any]]],
    epsg: int,
    legacy_crs: bool,
) -> None:
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": mapping(geom), "properties": props}
            for geom, props in features
        ],
    }
    if legacy_crs:
        collection["crs"] = {
            "type": "name",
            "properties": {"name": f"urn:ogc:def:crs:EPSG::{epsg}"},
        }
    with path.open("w", encoding="utf-8") as fp:
        json.dump(collection, fp, ensure_ascii=False, indent=1)


def write_outputs(
    outdir: Path,
    features: Sequence[Feature],
    site_map: Polygon,
    transform: PlanTransform,
) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    to_wgs84 = Transformer.from_crs(
        CRS.from_epsg(transform.epsg), CRS.from_epsg(4326), always_xy=True
    )

    def as_wgs84(polygon: Polygon) -> Polygon:
        return Polygon([to_wgs84.transform(x, y) for x, y in polygon.exterior.coords])

    written: list[Path] = []
    groups = {
        "buildings": [f for f in features if f.layer in {"tower", "podium"}],
        "context_buildings": [f for f in features if f.layer == "context"],
    }
    for name, group in groups.items():
        if not group:
            continue
        pairs = [(f.polygon_map, dict(f.attributes, area_m2=round(f.polygon_map.area, 1),
                                      gfa_m2=round(f.polygon_map.area * int(f.attributes["floors"]), 1)))
                 for f in group]
        proj_path = outdir / f"daegyo_{name}_epsg{transform.epsg}.geojson"
        wgs_path = outdir / f"daegyo_{name}_wgs84.geojson"
        geojson_dump(proj_path, pairs, transform.epsg, legacy_crs=True)
        geojson_dump(wgs_path, [(as_wgs84(g), p) for g, p in pairs], 4326, legacy_crs=False)
        written += [proj_path, wgs_path]

    site_props = {"name": "대지경계선(사업시행인가 대지)", "area_m2": round(site_map.area, 1)}
    site_proj = outdir / f"daegyo_site_boundary_epsg{transform.epsg}.geojson"
    site_wgs = outdir / "daegyo_site_boundary_wgs84.geojson"
    geojson_dump(site_proj, [(site_map, site_props)], transform.epsg, legacy_crs=True)
    geojson_dump(site_wgs, [(as_wgs84(site_map), site_props)], 4326, legacy_crs=False)
    written += [site_proj, site_wgs]

    csv_path = outdir / "daegyo_buildings.csv"
    columns = [
        "id", "dong", "part", "use", "floors", "floor_h_m", "roof_m",
        "height_m", "base_elev_m", "top_elev_m", "area_m2", "gfa_m2", "layer", "source", "wkt",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=columns)
        writer.writeheader()
        for feature in features:
            row = dict(feature.attributes)
            row["layer"] = feature.layer
            row["area_m2"] = round(feature.polygon_map.area, 1)
            row["gfa_m2"] = round(feature.polygon_map.area * int(feature.attributes["floors"]), 1)
            row["wkt"] = feature.polygon_map.wkt
            writer.writerow({key: row.get(key, "") for key in columns})
    written.append(csv_path)

    prj_path = outdir / "daegyo_buildings.prj"
    prj_path.write_text(CRS.from_epsg(transform.epsg).to_wkt(), encoding="utf-8")
    written.append(prj_path)
    csvt_path = outdir / "daegyo_buildings.csvt"
    csvt_path.write_text(
        '"String","String","String","String","Integer","Real","Real","Real","Real","Real",'
        '"Real","Real","String","String","WKT"\n',
        encoding="utf-8",
    )
    written.append(csvt_path)
    return written


def write_preview_svg(path: Path, config: dict[str, Any], features: Sequence[Feature]) -> Path:
    """배치도 이미지와 동일한 픽셀 좌표계로 트레이싱 결과를 SVG로 그린다.

    원본 배치도 이미지 위에 100% 크기로 겹쳐 보면 트레이싱 오차를 눈으로 검수할 수 있다.
    """
    width, height = config.get("image_size_px", [1500, 1152])
    fills = {"tower": "#d1495b", "podium": "#4c956c", "context": "#8d99ae"}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="none"/>',
    ]
    site = Polygon(config["site_boundary_px"])
    site_pts = " ".join(f"{x},{y}" for x, y in site.exterior.coords)
    parts.append(
        f'<polygon points="{site_pts}" fill="none" stroke="#e63946" stroke-width="3" '
        'stroke-dasharray="10 6"/>'
    )
    for feature in features:
        pts = " ".join(f"{x},{y}" for x, y in feature.polygon_px.exterior.coords)
        fill = fills.get(feature.layer, "#888888")
        opacity = 0.30 if feature.layer == "context" else 0.55
        parts.append(
            f'<polygon points="{pts}" fill="{fill}" fill-opacity="{opacity}" '
            f'stroke="{fill}" stroke-width="1.5"/>'
        )
        centroid = feature.polygon_px.centroid
        label = f"{feature.attributes['id']} / {feature.attributes['floors']}F"
        parts.append(
            f'<text x="{centroid.x:.0f}" y="{centroid.y:.0f}" font-size="13" '
            f'font-family="sans-serif" fill="#111111" text-anchor="middle">{label}</text>'
        )
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def build_report(
    config: dict[str, Any],
    transform: PlanTransform,
    area_scale: float,
    validation: Validation,
    features: Sequence[Feature],
) -> str:
    approval = config["approval"]
    lines: list[str] = []
    lines.append("# 대교아파트 배치도 폴리곤 생성 결과")
    lines.append("")
    lines.append(f"- 사업: {config['meta']['project']}")
    lines.append(f"- 대상 좌표계: EPSG:{transform.epsg}")
    lines.append(f"- 도면 축척: {transform.scale_m_per_px:.5f} m/px "
                 f"(대지면적 기준 산정값 {area_scale:.5f} m/px)")
    lines.append(f"- 배치도 상단 방향 방위각: {transform.plan_up_azimuth_deg:.2f}°")
    lines.append("")
    lines.append("## 인가 제원 대비 검증")
    lines.append("")
    lines.append("| 항목 | 인가 제원 | 폴리곤 산출값 | 편차 |")
    lines.append("| --- | ---: | ---: | ---: |")
    lines.append(
        f"| 대지면적 | {approval['site_area_m2']:,.1f} ㎡ | {validation.site_area_m2:,.1f} ㎡ | "
        f"{(validation.site_area_m2 - approval['site_area_m2']) / approval['site_area_m2'] * 100:+.2f}% |"
    )
    lines.append(
        f"| 건폐율 | {validation.approved_coverage_pct:.2f}% | {validation.coverage_pct:.2f}% | "
        f"{validation.coverage_dev_pct:+.2f}% |"
    )
    lines.append(
        f"| 용적률 | {validation.approved_far_pct:.2f}% | {validation.far_pct:.2f}% | "
        f"{validation.far_dev_pct:+.2f}% |"
    )
    lines.append(
        f"| 지상 연면적 | {approval['site_area_m2'] * approval['floor_area_ratio_pct'] / 100:,.0f} ㎡ "
        f"| {validation.gfa_m2:,.0f} ㎡ | - |"
    )
    lines.append("")
    lines.append("## 동별 제원")
    lines.append("")
    lines.append("| 동 | 존 | 층수 | 건축면적(㎡) | 높이(m, G.L.기준) | 최고고(EL, m) |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for feature in features:
        if feature.layer != "tower":
            continue
        attrs = feature.attributes
        lines.append(
            f"| {attrs['dong']} | {attrs['part']} | {attrs['floors']} | "
            f"{feature.polygon_map.area:,.0f} | {attrs['height_m']:,.1f} | {attrs['top_elev_m']:,.1f} |"
        )
    lines.append("")
    lines.append("## 유의사항")
    lines.append("")
    lines.append(
        "- 폴리곤 좌표는 배치도 래스터 이미지를 육안 트레이싱한 값으로 형상 정밀도는 ±2~3m 수준입니다."
    )
    lines.append(
        f"- 배치도 표기 최고층수(48F)와 인가 제원 최고층수({approval['max_floors_approved']}F)가 "
        "다르므로, 최종 인가도면 확인 후 data/daegyo_site_plan.json 의 floors 값을 갱신하십시오."
    )
    lines.append(
        "- 기본 앵커 좌표는 개략값입니다. `--control-points` 옵션으로 지적도/항공사진 기준 "
        "2점을 입력하면 축척·회전·위치가 정밀 보정됩니다."
    )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)

    transform, area_scale = build_transform(config, args)
    site_map = transform_polygon(Polygon(config["site_boundary_px"]), transform)

    features = make_features(config, include_context=not args.no_context)
    for feature in features:
        feature.polygon_map = transform_polygon(feature.polygon_px, transform)

    validation = validate(config, features, site_map)
    written = write_outputs(args.outdir, features, site_map, transform)

    written.append(
        write_preview_svg(args.outdir / "daegyo_trace_preview.svg", config, features)
    )

    report = build_report(config, transform, area_scale, validation, features)
    report_path = args.outdir / "daegyo_polygon_report.md"
    report_path.write_text(report, encoding="utf-8")
    written.append(report_path)

    print(report)
    print("생성 파일:")
    for path in written:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
