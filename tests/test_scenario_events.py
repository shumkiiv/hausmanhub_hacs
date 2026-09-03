"""Tests for event-driven scenario triggers without Home Assistant runtime."""

from __future__ import annotations

import asyncio
import unittest
from types import MappingProxyType
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from custom_components.hausman_hub.domain.scenarios import ScenarioComparison
from custom_components.hausman_hub.scenario_events import (
    _StateTriggerCoordinator,
    event_trigger_matches,
    state_level_matches,
    state_trigger_matches,
)
from custom_components.hausman_hub.application.scenario_command_context import (
    ScenarioCommandContextRegistry,
)
from custom_components.hausman_hub.manual_light_off_protection_events import (
    ManualLightOffProtectionEventListener,
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
    async def test_manual_trigger_and_protection_listener_read_automatic_context_independently(self) -> None:
        """Changing lookup back to consuming would make listener order unsafe."""

        service = SimpleNamespace(async_run_scenario=AsyncMock())
        contexts = ScenarioCommandContextRegistry(
            context_factory=lambda: SimpleNamespace(id="automatic.shared", parent_id=None)
        )
        context = contexts.create("switch.wall", "on")
        coordinator = _StateTriggerCoordinator(
            SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on"))),
            service,
            contexts,
        )
        protection = SimpleNamespace(async_note_state_transition=AsyncMock())
        listener = ManualLightOffProtectionEventListener(
            protection, contexts, {"switch.wall"}
        )
        item = (
            "system-tambur-adaptive-controller",
            "manual_chandelier_on",
            "switch.wall",
            "wall_target",
            "state",
            ScenarioComparison.EQUALS,
            "on",
            0,
            0,
            0,
            True,
        )
        event = SimpleNamespace(
            data={"entity_id": "switch.wall", "old_state": _state("off"), "new_state": _state("on")},
            context=context,
        )

        await coordinator.async_handle(
            item, event.data["old_state"], event.data["new_state"], event.context
        )
        await listener.async_handle(event)

        service.async_run_scenario.assert_not_awaited()
        attribution = protection.async_note_state_transition.await_args.args[3]
        self.assertEqual("automatic", attribution.source)
    async def test_physical_tambur_switch_is_classified_as_manual(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        coordinator = _StateTriggerCoordinator(hass, service)
        item = (
            "system-tambur-adaptive-controller",
            "manual_chandelier_on",
            "switch.wall",
            "wall_target",
            "state",
            ScenarioComparison.EQUALS,
            "on",
            0,
            0,
            0,
            True,
        )

        await coordinator.async_handle(item, _state("off"), _state("on"))

        context = service.async_run_scenario.await_args.kwargs["trigger_context"]
        self.assertEqual("manual", context["source"])
        self.assertEqual("manual_chandelier_on", context["trigger_id"])

    async def test_automatic_power_context_suppresses_manual_trigger(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        command_contexts = SimpleNamespace(match=lambda *_: object())
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        coordinator = _StateTriggerCoordinator(hass, service, command_contexts)
        item = (
            "system-tambur-adaptive-controller",
            "manual_chandelier_on",
            "switch.wall",
            "wall_target",
            "state",
            ScenarioComparison.EQUALS,
            "on",
            0,
            0,
            0,
            True,
        )

        await coordinator.async_handle(
            item,
            _state("off"),
            _state("on"),
            SimpleNamespace(id="automatic.1"),
        )

        service.async_run_scenario.assert_not_awaited()

    async def test_automatic_context_does_not_consume_unrelated_trigger(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        command_contexts = SimpleNamespace(consume=Mock(return_value=True))
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        coordinator = _StateTriggerCoordinator(hass, service, command_contexts)
        item = (
            "another-scenario",
            "relay_on",
            "switch.wall",
            "wall_target",
            "state",
            ScenarioComparison.EQUALS,
            "on",
            0,
            0,
            0,
            True,
        )

        await coordinator.async_handle(
            item,
            _state("off"),
            _state("on"),
            SimpleNamespace(id="automatic.1"),
        )

        command_contexts.consume.assert_not_called()
        service.async_run_scenario.assert_awaited_once()

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
            "motion_sensor",
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

        service.async_run_scenario.assert_awaited_once_with(
            "night_light",
            trigger_context={
                "source": "device_state",
                "trigger_id": "motion",
                "target_id": "motion_sensor",
                "old_value": "off",
                "new_value": "on",
                "recovery": False,
            },
        )

    async def test_cooldown_suppresses_repeated_transition(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        coordinator = _StateTriggerCoordinator(hass, service)
        item = (
            "night_light",
            "motion",
            "binary_sensor.motion",
            "motion_sensor",
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

        service.async_run_scenario.assert_awaited_once_with(
            "night_light",
            trigger_context={
                "source": "device_state",
                "trigger_id": "motion",
                "target_id": "motion_sensor",
                "old_value": "off",
                "new_value": "on",
                "recovery": False,
            },
        )

    async def test_recovery_transition_is_marked_in_trigger_context(self) -> None:
        service = SimpleNamespace(async_run_scenario=AsyncMock())
        hass = SimpleNamespace(states=SimpleNamespace(get=lambda _: _state("on")))
        coordinator = _StateTriggerCoordinator(hass, service)
        item = (
            "recovery_notice",
            "availability_changed",
            "binary_sensor.motion",
            "motion_sensor",
            "state",
            ScenarioComparison.CHANGED,
            None,
            0,
            0,
            0,
            False,
        )

        await coordinator.async_handle(item, _state("unavailable"), _state("on"))

        service.async_run_scenario.assert_awaited_once_with(
            "recovery_notice",
            trigger_context={
                "source": "device_state",
                "trigger_id": "availability_changed",
                "target_id": "motion_sensor",
                "old_value": "unavailable",
                "new_value": "on",
                "recovery": True,
            },
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
