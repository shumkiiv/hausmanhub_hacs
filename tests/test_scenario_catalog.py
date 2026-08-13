"""Tests for the HausmanHub scenario device/action catalog."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.scenario_catalog import (
    SCENARIO_CATALOG_DOMAINS,
    _domain_actions,
    _number_range,
    _stable_target_id_from_entity,
)


class ScenarioCatalogPureTest(unittest.TestCase):
    """Test catalog helpers that do not need Home Assistant."""

    def test_allowed_domains(self) -> None:
        self.assertEqual(
            SCENARIO_CATALOG_DOMAINS,
            {
                "button",
                "climate",
                "cover",
                "fan",
                "humidifier",
                "light",
                "lock",
                "media_player",
                "number",
                "switch",
                "vacuum",
                "valve",
                "water_heater",
            },
        )

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

    def test_number_action_accepts_only_bounded_value(self) -> None:
        actions = _domain_actions("number")
        self.assertEqual(1, len(actions))
        self.assertEqual("set_value", actions[0].action_id)
        self.assertEqual("set_value", actions[0].service)
        self.assertEqual(frozenset({"value"}), actions[0].allowed_fields)
        state = type(
            "State",
            (),
            {"attributes": {"min": 40, "max": 100, "step": 1}},
        )()
        self.assertEqual((40.0, 100.0, 1.0), _number_range(state))

    def test_number_range_rejects_missing_or_invalid_bounds(self) -> None:
        for attributes in (
            {},
            {"min": 40, "max": 40, "step": 1},
            {"min": 40, "max": 100, "step": 0},
            {"min": 40, "max": 100, "step": 100},
        ):
            with self.subTest(attributes=attributes):
                state = type("State", (), {"attributes": attributes})()
                self.assertIsNone(_number_range(state))

    def test_cover_position_accepts_value(self) -> None:
        actions = _domain_actions("cover")
        position = next(action for action in actions if action.action_id == "set_position")
        self.assertIn("value", position.allowed_fields)

    def test_extended_tablet_domains_have_semantic_actions(self) -> None:
        expected = {
            "button": {"press"},
            "humidifier": {"turn_on", "turn_off", "set_humidity"},
            "lock": {"lock", "unlock"},
            "vacuum": {"start", "pause", "stop", "return_home"},
            "valve": {"open_valve", "close_valve", "set_position"},
            "water_heater": {
                "turn_on",
                "turn_off",
                "set_temperature",
                "set_operation_mode",
            },
        }
        for domain, action_ids in expected.items():
            with self.subTest(domain=domain):
                self.assertEqual(
                    {action.action_id for action in _domain_actions(domain)},
                    action_ids,
                )

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
