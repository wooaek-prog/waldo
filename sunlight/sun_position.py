"""주어진 위도/경도/일시에 대한 태양 고도(elevation)·방위각(azimuth)을 계산한다.

NOAA Solar Calculator(https://gml.noaa.gov/grad/solcalc/)가 공개한 계산식을
그대로 옮긴 것으로, 실무에서 널리 쓰이는 정밀도(오차 약 0.01도 이내)를 갖는다.
대기굴절 보정은 적용하지 않는다(그림자 판정에는 영향이 미미함).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class SunPosition:
    elevation_deg: float
    """지평선 기준 태양 고도(도). 0 이하이면 태양이 지평선 아래에 있다."""
    azimuth_deg: float
    """정북(0도)에서 시계방향으로 잰 태양 방위각(도). 남쪽=180도."""

    @property
    def is_above_horizon(self) -> bool:
        return self.elevation_deg > 0.0

    def direction_enu(self) -> tuple[float, float, float]:
        """태양을 향하는 단위 벡터를 (East, North, Up) 로컬 좌표로 반환한다."""
        el = math.radians(self.elevation_deg)
        az = math.radians(self.azimuth_deg)
        cos_el = math.cos(el)
        east = cos_el * math.sin(az)
        north = cos_el * math.cos(az)
        up = math.sin(el)
        return (east, north, up)


def _julian_day(dt_utc: datetime) -> float:
    year, month = dt_utc.year, dt_utc.month
    day = (
        dt_utc.day
        + dt_utc.hour / 24.0
        + dt_utc.minute / 1440.0
        + dt_utc.second / 86400.0
    )
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day
        + b
        - 1524.5
    )


def solar_position(dt: datetime, latitude_deg: float, longitude_deg: float) -> SunPosition:
    """지역시(타임존 포함) 및 위경도로부터 태양 고도/방위각을 구한다.

    dt는 반드시 타임존 정보(tzinfo)를 가지고 있어야 한다.
    longitude_deg는 동경(+)/서경(-) 기준.
    """
    if dt.tzinfo is None:
        raise ValueError("dt must be timezone-aware")

    dt_utc = dt.astimezone(timezone.utc)
    jd = _julian_day(dt_utc)
    jc = (jd - 2451545.0) / 36525.0

    geom_mean_long_sun = math.radians(
        (280.46646 + jc * (36000.76983 + jc * 0.0003032)) % 360.0
    )
    geom_mean_anom_sun = math.radians(
        357.52911 + jc * (35999.05029 - 0.0001537 * jc)
    )
    eccent_earth_orbit = 0.016708634 - jc * (0.000042037 + 0.0000001267 * jc)

    sun_eq_of_ctr = (
        math.sin(geom_mean_anom_sun) * (1.914602 - jc * (0.004817 + 0.000014 * jc))
        + math.sin(2 * geom_mean_anom_sun) * (0.019993 - 0.000101 * jc)
        + math.sin(3 * geom_mean_anom_sun) * 0.000289
    )

    sun_true_long = math.degrees(geom_mean_long_sun) + sun_eq_of_ctr
    sun_app_long = math.radians(
        sun_true_long - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * jc))
    )

    mean_obliq_ecliptic = (
        23.0
        + (26.0 + (21.448 - jc * (46.815 + jc * (0.00059 - jc * 0.001813))) / 60.0) / 60.0
    )
    obliq_corr = math.radians(
        mean_obliq_ecliptic + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * jc))
    )

    sun_declin = math.asin(math.sin(obliq_corr) * math.sin(sun_app_long))

    var_y = math.tan(obliq_corr / 2.0) ** 2
    l0 = math.degrees(geom_mean_long_sun)
    m = math.degrees(geom_mean_anom_sun)
    eq_of_time = 4 * math.degrees(
        var_y * math.sin(2 * math.radians(l0))
        - 2 * eccent_earth_orbit * math.sin(math.radians(m))
        + 4 * eccent_earth_orbit * var_y * math.sin(math.radians(m)) * math.cos(2 * math.radians(l0))
        - 0.5 * var_y * var_y * math.sin(4 * math.radians(l0))
        - 1.25 * eccent_earth_orbit * eccent_earth_orbit * math.sin(2 * math.radians(m))
    )

    time_offset_minutes = dt.utcoffset().total_seconds() / 60.0
    time_min_of_day = dt.hour * 60.0 + dt.minute + dt.second / 60.0
    true_solar_time = (
        time_min_of_day + eq_of_time + 4 * longitude_deg - time_offset_minutes
    ) % 1440.0

    hour_angle_deg = true_solar_time / 4.0 - 180.0
    if true_solar_time / 4.0 < 0:
        hour_angle_deg = true_solar_time / 4.0 + 180.0
    hour_angle = math.radians(hour_angle_deg)

    lat = math.radians(latitude_deg)
    zenith = math.acos(
        math.sin(lat) * math.sin(sun_declin)
        + math.cos(lat) * math.cos(sun_declin) * math.cos(hour_angle)
    )
    elevation_deg = 90.0 - math.degrees(zenith)

    denom = math.cos(lat) * math.sin(zenith)
    if abs(denom) < 1e-9:
        azimuth_deg = 180.0
    else:
        az_arg = (math.sin(lat) * math.cos(zenith) - math.sin(sun_declin)) / denom
        az_arg = max(-1.0, min(1.0, az_arg))
        if hour_angle_deg > 0:
            azimuth_deg = (math.degrees(math.acos(az_arg)) + 180.0) % 360.0
        else:
            azimuth_deg = (540.0 - math.degrees(math.acos(az_arg))) % 360.0

    return SunPosition(elevation_deg=elevation_deg, azimuth_deg=azimuth_deg)
