"""창문별 일조 시뮬레이션 및 '연속 2시간 / 총 4시간' 기준 판정.

기준: 동지(冬至)를 기준으로 09:00~15:00 사이 연속 2시간 이상, 또는
08:00~16:00 사이 총 4시간 이상의 일조를 확보하면 '적합'으로 판정한다.
이 수치는 주택건설기준 등에 관한 규정 및 각 지자체 건축조례에서 공동주택
일조 확보 기준으로 널리 쓰이는 값을 학교 교실 일조 검토에 참고 기준으로
준용한 것이며, 학교에 법적으로 강제되는 고정 수치는 아니다. 실제 인허가/
민원 대응 시에는 해당 교육청·지자체가 적용하는 기준을 확인해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .geometry import BuildingMass, any_blocked
from .model import SiteModel, Window
from .sun_position import SunPosition, solar_position

TOTAL_WINDOW_START_HOUR = 8
TOTAL_WINDOW_END_HOUR = 16
CONTINUOUS_WINDOW_START_HOUR = 9
CONTINUOUS_WINDOW_END_HOUR = 15
REQUIRED_TOTAL_HOURS = 4.0
REQUIRED_CONTINUOUS_HOURS = 2.0
MIN_SUN_FACING_COMPONENT = 0.02
"""창 법선과 태양방향의 내적이 이 값 이하이면 태양이 창을 스치거나 뒤쪽에
있는 것으로 보아 직사광이 없다고 판정한다(자기 건물에 의한 자기차폐)."""


@dataclass
class TimeSample:
    time: datetime
    sunlit: bool
    elevation_deg: float
    azimuth_deg: float


@dataclass
class WindowResult:
    window: Window
    samples: list[TimeSample] = field(default_factory=list)
    total_sunlit_hours: float = 0.0
    max_continuous_hours: float = 0.0
    meets_standard: bool = False

    @property
    def total_ok(self) -> bool:
        return self.total_sunlit_hours >= REQUIRED_TOTAL_HOURS

    @property
    def continuous_ok(self) -> bool:
        return self.max_continuous_hours >= REQUIRED_CONTINUOUS_HOURS


def _is_window_sunlit(
    window: Window,
    obstructions: list[BuildingMass],
    sun: SunPosition,
) -> bool:
    if not sun.is_above_horizon:
        return False
    east, north, up = sun.direction_enu()
    nx, ny = window.normal
    facing = east * nx + north * ny
    if facing <= MIN_SUN_FACING_COMPONENT:
        return False
    return not any_blocked(
        obstructions,
        window.position,
        (east, north, up),
        exclude_ids=(window.building_id,),
    )


def _max_consecutive_true(flags: list[bool]) -> int:
    best = current = 0
    for flag in flags:
        if flag:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def analyze_window(
    window: Window,
    obstructions: list[BuildingMass],
    day: date_cls,
    tz: ZoneInfo,
    latitude: float,
    longitude: float,
    interval_minutes: int,
) -> WindowResult:
    result = WindowResult(window=window)

    start = datetime(day.year, day.month, day.day, TOTAL_WINDOW_START_HOUR, 0, tzinfo=tz)
    end = datetime(day.year, day.month, day.day, TOTAL_WINDOW_END_HOUR, 0, tzinfo=tz)
    cont_start = datetime(
        day.year, day.month, day.day, CONTINUOUS_WINDOW_START_HOUR, 0, tzinfo=tz
    )
    cont_end = datetime(
        day.year, day.month, day.day, CONTINUOUS_WINDOW_END_HOUR, 0, tzinfo=tz
    )

    step = timedelta(minutes=interval_minutes)
    t = start
    sunlit_slots = 0
    total_slots = 0
    continuous_flags: list[bool] = []

    while t < end:
        sun = solar_position(t, latitude, longitude)
        sunlit = _is_window_sunlit(window, obstructions, sun)
        result.samples.append(
            TimeSample(
                time=t, sunlit=sunlit, elevation_deg=sun.elevation_deg, azimuth_deg=sun.azimuth_deg
            )
        )
        total_slots += 1
        if sunlit:
            sunlit_slots += 1
        if cont_start <= t < cont_end:
            continuous_flags.append(sunlit)
        t += step

    result.total_sunlit_hours = sunlit_slots * interval_minutes / 60.0
    result.max_continuous_hours = _max_consecutive_true(continuous_flags) * interval_minutes / 60.0
    result.meets_standard = result.total_ok or result.continuous_ok
    return result


def analyze_site(
    model: SiteModel,
    day: date_cls,
    interval_minutes: int = 10,
) -> list[WindowResult]:
    tz = ZoneInfo(model.location.timezone)
    results = []
    for window in model.windows:
        results.append(
            analyze_window(
                window,
                model.obstructions,
                day,
                tz,
                model.location.latitude,
                model.location.longitude,
                interval_minutes,
            )
        )
    return results
