"""Tests for the HausmanHub scenario device/action catalog."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.scenario_catalog import (
    SCENARIO_CATALOG_DOMAINS,
    _domain_actions,
    _stable_target_id_from_entity,
)


class ScenarioCatalogPureTest(unittest.TestCase):
    """Test catalog helpers that do not need Home Assistant."""

    def test_allowed_domains(self) -> None:
        self.assertIn("light", SCENARIO_CATALOG_DOMAINS)
        self.assertIn("switch", SCENARIO_CATALOG_DOMAINS)
        self.assertIn("climate", SCENARIO_CATALOG_DOMAINS)

    def test_light_actions_include_brightness(self) -> None:
        actions = _domain_actions("light")
        ids = {action.action_id for action in actions}
        self.assertEqual(ids, {"turn_on", "turn_off", "toggle", "set_brightness"})
        brightness = next(action for action in actions if action.action_id == "set_brightness")
        self.assertEqual(brightness.domain, "light")
        self.assertEqual(brightness.service, "turn_on")
        self.assertIn("value", brightness.allowed_fields)

    def test_switch_actions_do_not_accept_value(self) -> None:
        actions = _domain_actions("switch")
        for action in actions:
            self.assertNotIn("value", action.allowed_fields)

    def test_cover_position_accepts_value(self) -> None:
        actions = _domain_actions("cover")
        position = next(action for action in actions if action.action_id == "set_position")
        self.assertIn("value", position.allowed_fields)

    def test_unknown_domain_has_no_actions(self) -> None:
        self.assertEqual(_domain_actions("sensor"), ())

    def test_stable_target_id_is_deterministic(self) -> None:
        self.assertEqual(
            _stable_target_id_from_entity("light.living_room"),
            _stable_target_id_from_entity("light.living_room"),
        )
        self.assertNotEqual(
            _stable_target_id_from_entity("light.living_room"),
            _stable_target_id_from_entity("switch.kitchen"),
        )


if __name__ == "__main__":
    unittest.main()
