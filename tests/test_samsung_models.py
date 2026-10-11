"""Model-year parsing. Families/years follow Samsung's TV Model Groups table
(developer.samsung.com/smarttv/develop/specifications/tv-model-groups.html)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import samsung_models  # noqa: E402


class ModelYearTests(unittest.TestCase):
    def test_known_families(self):
        cases = {"UE43RU7092UXXH": (2019, "LED"), "UE55MU6172UXXU": (2017, "LED"), "UE55NU7400": (2018, "LED"),
                 "UE48J6200": (2015, "LED"), "UE55KS7000": (2016, "LED"), "GU43TU7079": (2020, "LED"),
                 "UE43AU7100": (2021, "LED"), "UE43CU7100KXXU": (2023, "LED"), "UE55U8000F": (2025, "LED"),
                 "QE55Q60RATXXU": (2019, "QLED"), "QN65Q80T": (2020, "QLED"), "QE65QN90B": (2022, "QLED"),
                 "QE65QN900C": (2023, "QLED"), "QE55Q7FN": (2018, "QLED"), "QE55Q8C": (2017, "QLED"),
                 "QE55S95B": (2022, "OLED"), "QE65S90C": (2023, "OLED"), "QE55LS03T": (2020, "Lifestyle"),
                 "QE55LS03BAU": (2022, "Lifestyle"), "UE55LS003": (2017, "Lifestyle")}
        for model, (year, family) in cases.items():
            with self.subTest(model=model):
                parsed = samsung_models.model_year(model)
                self.assertEqual((parsed["year"], parsed["family"]), (year, family))
                self.assertEqual(parsed["baseline_tizen"], samsung_models.BASELINE_TIZEN[year])

    def test_unknown_or_ambiguous_models_have_no_year(self):
        # Q7F is both a 2017 and a 2025 QLED name; single letters inside free text are never guessed.
        for model in ("QE55Q7F", "XYZ123", "", None, "RU", "UE43XU7092", "19_MUSEL_UHD", "UE43RU7092 extra"):
            with self.subTest(model=model):
                self.assertIsNone(samsung_models.model_year(model)["year"])


if __name__ == "__main__":
    unittest.main()
