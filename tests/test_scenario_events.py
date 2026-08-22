"""Tests for event-driven scenario triggers without Home Assistant runtime."""

from __future__ import annotations

import asyncio
import unittest
from types import MappingProxyType
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.hausman_hub.domain.scenarios import ScenarioComparison
from custom_components.hausman_hub.scenario_events import (
    _StateTriggerCoordinator,
    event_trigger_matches,
    state_level_matches,
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

    def test_equals_ignores_startup_and_availability_recovery(self) -> None:
        for old_state in (None, _state("unknown"), _state("unavailable")):
            with self.subTest(old_state=old_state):
                self.assertFalse(
                    state_trigger_matches(
                        old_state,
                        _state("on"),
                        "state",
                        ScenarioComparison.EQUALS,
                        "on",
                    )
                )

    def test_changed_ignores_availability_recovery(self) -> None:
        self.assertFalse(
            state_trigger_matches(
                _state("unavailable"),
                _state("on"),
                "state",
                ScenarioComparison.CHANGED,
                None,
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

    def test_recovery_can_be_explicitly_enabled(self) -> None:
        self.assertTrue(
            state_trigger_matches(
                _state("unavailable"),
                _state("on"),
                "state",
                ScenarioComparison.EQUALS,
                "on",
                ignore_recovery=False,
            )
        )

    def test_level_match_rechecks_delayed_threshold(self) -> None:
        self.assertTrue(
            state_level_matches(
                _state("22"), "state", ScenarioComparison.ABOVE, 20
            )
        )
        self.assertFalse(
            state_level_matches(
                _state("19"), "state", ScenarioComparison.ABOVE, 20
            )
        )


class StateTriggerCoordinatorTest(unittest.IsolatedAsyncioTestCase):
    async def test_for_duration_rechecks_state_before_run(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        hass = SimpleNamespace(
            states=SimpleNamespace(get=lambda _: _state("on")),
            async_create_task=lambda coroutine: asyncio.create_task(coroutine),
        )
        coordinator = _StateTriggerCoordinator(hass, service)
        item = (
            "night_light",
            "motion",
            "binary_sensor.motion",
            "state",
            ScenarioComparison.EQUALS,
            "on",
            10,
            2,
            0,
            True,
        )

        with patch(
            "custom_components.hausman_hub.scenario_events.asyncio.sleep",
            new=AsyncMock(),
        ):
            await coordinator.async_handle(item, _state("off"), _state("on"))
            await asyncio.gather(*coordinator._pending.values())

        service.async_run_scenario.assert_awaited_once_with("night_light")

    async def test_cooldown_suppresses_repeated_transition(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        coordinator = _StateTriggerCoordinator(hass, service)
        item = (
            "night_light",
            "motion",
            "binary_sensor.motion",
            "state",
            ScenarioComparison.EQUALS,
            "on",
            0,
            0,
            60,
            True,
        )

        with patch(
            "custom_components.hausman_hub.scenario_events.time.monotonic",
            return_value=100.0,
        ):
            await coordinator.async_handle(item, _state("off"), _state("on"))
            await coordinator.async_handle(item, _state("off"), _state("on"))

        service.async_run_scenario.assert_awaited_once_with("night_light")


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
