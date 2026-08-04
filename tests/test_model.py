import unittest
from pathlib import Path

from sunlight.model import load_site_model

CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "site_yeouido_40_4.json"


class LoadSiteModelTests(unittest.TestCase):
    def setUp(self):
        self.model = load_site_model(CONFIG_PATH)

    def test_obstructions_include_tower_and_school_buildings(self):
        ids = {ob.id for ob in self.model.obstructions}
        self.assertIn("hwarang_new_tower", ids)
        self.assertIn("yeoui_elem_main", ids)
        self.assertIn("yeoui_middle_main", ids)
        self.assertIn("yeoui_girls_high_main", ids)

    def test_windows_generated_for_each_school(self):
        by_school = self.model.window_by_school()
        self.assertEqual(len(by_school), 3)
        self.assertEqual(len(by_school["yeoui_elem"]), 6 * 4)
        self.assertEqual(len(by_school["yeoui_middle"]), 8 * 5)
        self.assertEqual(len(by_school["yeoui_girls_high"]), 8 * 5)

    def test_window_normal_is_unit_vector(self):
        w = self.model.windows[0]
        nx, ny = w.normal
        self.assertAlmostEqual(nx * nx + ny * ny, 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
