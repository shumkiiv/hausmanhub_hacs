"""Visual identity contract for the energy section."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PANEL_JS = (
    ROOT
    / "custom_components"
    / "hausman_hub"
    / "frontend"
    / "hausman-hub-panel.js"
)


class EnergyIconContractTest(unittest.TestCase):
    def test_energy_has_a_dedicated_meter_not_the_scenario_bolt(self) -> None:
        source = PANEL_JS.read_text(encoding="utf-8")

        self.assertIn("const ICON_STROKE_PATHS", source)
        self.assertIn('energy: [', source)
        self.assertIn('"M8 2.75h8c1.8 0 3.25 1.45', source)
        self.assertIn('setAttr(path, "stroke", "currentColor")', source)
        self.assertIn('setAttr(path, "stroke-width", "1.8")', source)
        self.assertIn('bolt: "M11 21h-1l1-7H7.5', source)
        self.assertNotIn('energy: "M13 2 4.5 13H11l-1 9', source)


if __name__ == "__main__":
    unittest.main()
