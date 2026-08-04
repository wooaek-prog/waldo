import datetime as dt
import unittest
from pathlib import Path

from sunlight.analysis import analyze_site
from sunlight.model import load_site_model

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "site_yeouido_40_4.json"


class AnalyzeSiteTests(unittest.TestCase):
    def test_winter_solstice_produces_some_noncompliant_windows(self):
        model = load_site_model(CONFIG_PATH)
        results = analyze_site(model, dt.date(2026, 12, 22), interval_minutes=15)

        self.assertEqual(len(results), len(model.windows))
        for r in results:
            self.assertGreaterEqual(r.total_sunlit_hours, 0.0)
            self.assertLessEqual(r.total_sunlit_hours, 8.0)
            self.assertGreaterEqual(r.max_continuous_hours, 0.0)
            self.assertLessEqual(r.max_continuous_hours, 6.0)

        # 175m 신축동과 정면으로 마주보는 초등학교 창문 일부는 동지에 기준
        # (연속2h 또는 총4h)에 미달해야 한다(현실 민원 사례와 부합).
        fail_count = sum(1 for r in results if not r.meets_standard)
        self.assertGreater(fail_count, 0)
        self.assertTrue(all(r.window.school_id == "yeoui_elem" for r in results if not r.meets_standard))

    def test_removing_tower_improves_or_maintains_sunlight(self):
        model = load_site_model(CONFIG_PATH)
        day = dt.date(2026, 12, 22)
        with_tower = analyze_site(model, day, interval_minutes=15)

        model.obstructions[:] = [
            ob for ob in model.obstructions if ob.id != "hwarang_new_tower"
        ]
        without_tower = analyze_site(model, day, interval_minutes=15)

        total_with = sum(r.total_sunlit_hours for r in with_tower)
        total_without = sum(r.total_sunlit_hours for r in without_tower)
        self.assertGreaterEqual(total_without, total_with)


if __name__ == "__main__":
    unittest.main()
