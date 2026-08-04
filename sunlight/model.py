"""부지/신축 매싱/인접 학교 창문 정보를 JSON 설정에서 읽어들이는 모델 계층."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geometry import BuildingMass


@dataclass(frozen=True)
class SiteLocation:
    name: str
    parcel: str
    address: str
    latitude: float
    longitude: float
    timezone: str
    notes: str = ""


@dataclass(frozen=True)
class Window:
    """일조 판정 대상이 되는 학교 건물의 창문(교실) 표본점."""

    id: str
    school_id: str
    school_name: str
    building_id: str
    facade_name: str
    floor: int
    position: tuple[float, float, float]
    normal: tuple[float, float]
    """창이 바라보는 바깥쪽 방향의 수평 단위 법선벡터 (East, North)."""


@dataclass(frozen=True)
class SiteModel:
    location: SiteLocation
    obstructions: list[BuildingMass]
    windows: list[Window]

    def window_by_school(self) -> dict[str, list[Window]]:
        grouped: dict[str, list[Window]] = {}
        for w in self.windows:
            grouped.setdefault(w.school_id, []).append(w)
        return grouped


def _normalize2(vec: tuple[float, float]) -> tuple[float, float]:
    x, y = vec
    length = math.hypot(x, y)
    if length < 1e-9:
        raise ValueError(f"zero-length normal vector: {vec}")
    return (x / length, y / length)


def _load_footprint(raw: list[list[float]]) -> tuple[tuple[float, float], ...]:
    return tuple((float(p[0]), float(p[1])) for p in raw)


def _generate_facade_windows(
    *,
    building_id: str,
    school_id: str,
    school_name: str,
    facade_name: str,
    wall: list[list[float]],
    normal: list[float],
    base_z: float,
    floor_height: float,
    num_floors: int,
    windows_per_floor: int,
    sill_height: float,
    start_floor: int = 1,
    edge_margin: float = 1.0,
) -> list[Window]:
    (x1, y1), (x2, y2) = wall[0], wall[1]
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 2 * edge_margin:
        usable_start, usable_len = 0.0, length
    else:
        usable_start, usable_len = edge_margin, length - 2 * edge_margin

    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    nx, ny = _normalize2((normal[0], normal[1]))

    windows: list[Window] = []
    for floor_idx in range(num_floors):
        floor_no = start_floor + floor_idx
        z = base_z + floor_idx * floor_height + sill_height
        for slot in range(windows_per_floor):
            if windows_per_floor == 1:
                frac = 0.5
            else:
                frac = slot / (windows_per_floor - 1)
            dist = usable_start + frac * usable_len
            px = x1 + ux * dist
            py = y1 + uy * dist
            windows.append(
                Window(
                    id=f"{building_id}:{facade_name}:F{floor_no}:{slot + 1}",
                    school_id=school_id,
                    school_name=school_name,
                    building_id=building_id,
                    facade_name=facade_name,
                    floor=floor_no,
                    position=(px, py, z),
                    normal=(nx, ny),
                )
            )
    return windows


def load_site_model(path: str | Path) -> SiteModel:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))

    site_raw = data["site"]
    location = SiteLocation(
        name=site_raw["name"],
        parcel=site_raw["parcel"],
        address=site_raw.get("address", ""),
        latitude=float(site_raw["latitude"]),
        longitude=float(site_raw["longitude"]),
        timezone=site_raw.get("timezone", "Asia/Seoul"),
        notes=site_raw.get("notes", ""),
    )

    obstructions: list[BuildingMass] = []
    for ob in data.get("obstructions", []):
        obstructions.append(
            BuildingMass(
                id=ob["id"],
                name=ob["name"],
                footprint=_load_footprint(ob["footprint"]),
                base_z=float(ob.get("base_z", 0.0)),
                height=float(ob["height"]),
            )
        )

    windows: list[Window] = []
    for school in data.get("schools", []):
        school_id = school["id"]
        school_name = school["name"]
        for building in school.get("buildings", []):
            building_id = building["id"]
            obstructions.append(
                BuildingMass(
                    id=building_id,
                    name=building.get("name", f"{school_name} {building_id}"),
                    footprint=_load_footprint(building["footprint"]),
                    base_z=float(building.get("base_z", 0.0)),
                    height=float(building["height"]),
                )
            )
            for facade in building.get("facades", []):
                windows.extend(
                    _generate_facade_windows(
                        building_id=building_id,
                        school_id=school_id,
                        school_name=school_name,
                        facade_name=facade["name"],
                        wall=facade["wall"],
                        normal=facade["normal"],
                        base_z=float(building.get("base_z", 0.0)),
                        floor_height=float(facade["floor_height"]),
                        num_floors=int(facade["num_floors"]),
                        windows_per_floor=int(facade["windows_per_floor"]),
                        sill_height=float(facade.get("sill_height", 1.2)),
                        start_floor=int(facade.get("start_floor", 1)),
                        edge_margin=float(facade.get("edge_margin", 1.0)),
                    )
                )

    return SiteModel(location=location, obstructions=obstructions, windows=windows)
