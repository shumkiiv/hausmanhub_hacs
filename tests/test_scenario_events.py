"""Tests for event-driven scenario triggers without Home Assistant runtime."""

from __future__ import annotations

import unittest
from types import MappingProxyType
from types import SimpleNamespace

from custom_components.hausman_hub.domain.scenarios import ScenarioComparison
from custom_components.hausman_hub.scenario_events import (
    event_trigger_matches,
    state_trigger_matches,
)


def _state(value: object, **attributes: object) -> SimpleNamespace:
    return SimpleNamespace(state=value, attributes=attributes)


class StateTriggerMatchesTest(unittest.TestCase):
    def test_equals_runs_only_when_entering_value(self) -> None:
        self.assertTrue(
            state_trigger_matches(
                _state("off"), _state("on"), "state", ScenarioComparison.EQUALS, "on"
            )
        )
        self.assertFalse(
            state_trigger_matches(
                _state("on"), _state("on", battery=95), "state", ScenarioComparison.EQUALS, "on"
            )
        )

    def test_changed_compares_requested_attribute(self) -> None:
        self.assertTrue(
            state_trigger_matches(
                _state("on", temperature=20),
                _state("on", temperature=21),
                "temperature",
                ScenarioComparison.CHANGED,
                None,
            )
        )

    def test_numeric_threshold_fires_only_on_crossing(self) -> None:
        self.assertTrue(
            state_trigger_matches(
                _state("999"), _state("1001"), "state", ScenarioComparison.ABOVE, 1000
            )
        )
        self.assertFalse(
            state_trigger_matches(
                _state("1001"), _state("1002"), "state", ScenarioComparison.ABOVE, 1000
            )
        )
        self.assertTrue(
            state_trigger_matches(
                _state("56"), _state("54"), "state", ScenarioComparison.BELOW, 55
            )
        )

    def test_missing_new_value_does_not_run(self) -> None:
        self.assertFalse(
            state_trigger_matches(
                _state("off"), None, "state", ScenarioComparison.EQUALS, "on"
            )
        )


class EventTriggerMatchesTest(unittest.TestCase):
    def test_matches_exact_scalar_filter(self) -> None:
        self.assertTrue(
            event_trigger_matches(
                {"device_id": "button-kids", "action": "single"},
                {"device_id": "button-kids", "action": "single"},
            )
        )

    def test_rejects_missing_nested_and_type_coerced_fields(self) -> None:
        expected = {"button": 1}
        self.assertFalse(event_trigger_matches({}, expected))
        self.assertFalse(event_trigger_matches({"button": "1"}, expected))
        self.assertFalse(event_trigger_matches({"button": {"id": 1}}, expected))

    def test_accepts_read_only_mapping_from_home_assistant(self) -> None:
        self.assertTrue(
            event_trigger_matches(
                MappingProxyType({"device_id": "button-kids"}),
                {"device_id": "button-kids"},
            )
        )
