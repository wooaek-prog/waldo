import unittest

from sunlight.geometry import BuildingMass, any_blocked, point_in_polygon, polygon_ray_xy_intervals

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


class PointInPolygonTests(unittest.TestCase):
    def test_inside(self):
        self.assertTrue(point_in_polygon((5, 5), SQUARE))

    def test_outside(self):
        self.assertFalse(point_in_polygon((15, 5), SQUARE))
        self.assertFalse(point_in_polygon((-1, 5), SQUARE))


class PolygonRayIntervalsTests(unittest.TestCase):
    def test_ray_through_square_center(self):
        intervals = polygon_ray_xy_intervals(SQUARE, (5, -10), (0, 1), t_max=100)
        self.assertEqual(len(intervals), 1)
        t0, t1 = intervals[0]
        self.assertAlmostEqual(t0, 10.0, places=6)
        self.assertAlmostEqual(t1, 20.0, places=6)

    def test_ray_missing_square(self):
        intervals = polygon_ray_xy_intervals(SQUARE, (50, -10), (0, 1), t_max=100)
        self.assertEqual(intervals, [])


class BuildingMassOcclusionTests(unittest.TestCase):
    def setUp(self):
        self.tower = BuildingMass(
            id="tower", name="tower", footprint=tuple(SQUARE), base_z=0.0, height=100.0
        )

    def test_low_sun_from_south_is_blocked_by_tall_tower(self):
        # 관측점: 탑 남쪽 20m, 지상 1.2m. 태양이 정남(방위 180)에서 낮은 고도로 뜬 상황.
        origin = (5.0, -20.0, 1.2)
        direction = (0.0, 1.0, 0.3)  # 대략 고도 ~17도, 정북 방향으로 진행
        self.assertTrue(self.tower.ray_blocked(origin, direction))

    def test_high_sun_clears_tower(self):
        origin = (5.0, -20.0, 1.2)
        direction = (0.0, 0.3, 5.0)  # 고도가 매우 높아 탑 위로 넘어감
        self.assertFalse(self.tower.ray_blocked(origin, direction))

    def test_direction_pointing_away_from_tower_not_blocked(self):
        origin = (5.0, -20.0, 1.2)
        direction = (0.0, -1.0, 0.3)  # 탑 반대 방향
        self.assertFalse(self.tower.ray_blocked(origin, direction))

    def test_any_blocked_respects_exclude_ids(self):
        origin = (5.0, -20.0, 1.2)
        direction = (0.0, 1.0, 0.3)
        self.assertFalse(
            any_blocked([self.tower], origin, direction, exclude_ids=("tower",))
        )
        self.assertTrue(any_blocked([self.tower], origin, direction))


if __name__ == "__main__":
    unittest.main()
