import math
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from sunlight.sun_position import solar_position

SEOUL_LAT = 37.5665
SEOUL_LON = 126.9780
KST = ZoneInfo("Asia/Seoul")


class SolarPositionTests(unittest.TestCase):
    def test_solar_noon_azimuth_is_roughly_south(self):
        # 한국 표준시(KST)는 동경 135도 기준이라 서울(약 127도)의 실제
        # 태양남중시각은 12:00보다 늦다(약 12:30 전후). 12:30으로 근사 검증.
        t = datetime(2026, 12, 22, 12, 30, tzinfo=KST)
        sun = solar_position(t, SEOUL_LAT, SEOUL_LON)
        self.assertAlmostEqual(sun.azimuth_deg, 180.0, delta=3.0)
        self.assertGreater(sun.elevation_deg, 0)

    def test_winter_solstice_noon_elevation_matches_formula(self):
        # 동지 태양남중고도 근사식: 90 - |위도 - 적위(-23.44)|
        t = datetime(2026, 12, 22, 12, 30, tzinfo=KST)
        sun = solar_position(t, SEOUL_LAT, SEOUL_LON)
        expected = 90 - abs(SEOUL_LAT - (-23.44))
        self.assertAlmostEqual(sun.elevation_deg, expected, delta=1.0)

    def test_summer_elevation_higher_than_winter(self):
        winter = solar_position(datetime(2026, 12, 22, 12, 30, tzinfo=KST), SEOUL_LAT, SEOUL_LON)
        summer = solar_position(datetime(2026, 6, 21, 12, 30, tzinfo=KST), SEOUL_LAT, SEOUL_LON)
        self.assertGreater(summer.elevation_deg, winter.elevation_deg)

    def test_night_elevation_is_negative(self):
        midnight = solar_position(datetime(2026, 12, 22, 0, 0, tzinfo=KST), SEOUL_LAT, SEOUL_LON)
        self.assertLess(midnight.elevation_deg, 0)
        self.assertFalse(midnight.is_above_horizon)

    def test_direction_enu_is_unit_vector(self):
        t = datetime(2026, 9, 21, 10, 0, tzinfo=KST)
        sun = solar_position(t, SEOUL_LAT, SEOUL_LON)
        e, n, u = sun.direction_enu()
        length = math.sqrt(e * e + n * n + u * u)
        self.assertAlmostEqual(length, 1.0, places=6)

    def test_requires_timezone_aware_datetime(self):
        with self.assertRaises(ValueError):
            solar_position(datetime(2026, 1, 1, 12, 0), SEOUL_LAT, SEOUL_LON)


if __name__ == "__main__":
    unittest.main()
