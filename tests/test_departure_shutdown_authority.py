"""Departure OFF is explicit authority, not an ordinary absence command."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from test_scenario_executor import _FakeCatalog, _FakeHass, _definition

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
)
from custom_components.hausman_hub.application.scenario_executor import ScenarioExecutor
from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
from custom_components.hausman_hub.domain.scenarios import ScenarioAction, ScenarioActionType


class DepartureShutdownAuthorityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = _FakeHass()
        self.catalog = _FakeCatalog()
        self.authority = ManualLightAuthority()
        self._state("on")
        self.authority.capture("light.living_room", "on", "service", "known-manual-on")

        async def dispatch(domain, service, data, **kwargs):
            self.assertEqual("light", domain)
            self.assertEqual("light.living_room", data["entity_id"])
            self._state("off" if service == "turn_off" else "on")

        self.hass.services.async_call.side_effect = dispatch
        self.executor = ScenarioExecutor(
            self.hass, self.catalog, self._nested,
            manual_light_authority=self.authority,
            readback_window_seconds=0.02, readback_interval_seconds=0.01,
        )

    def _state(self, value):
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state=value, attributes={}, last_updated=datetime.now(timezone.utc),
        )

    def _off(self):
        return _definition((ScenarioAction(
            id="off", type=ScenarioActionType.DEVICE_ACTION,
            target_id="device_1", action_id="turn_off",
        ),))

    async def _nested(self, scenario_id, **kwargs):
        definition = self._off() if scenario_id == "lights_off" else _definition((
            ScenarioAction(id="lights", type=ScenarioActionType.RUN_SCENARIO,
                           scenario_id="lights_off"),
        ))
        return await self.executor.async_execute(
            definition, "nested-" + scenario_id, scenario_id=scenario_id,
            visited_scenarios=kwargs.get("visited"),
            trigger_context=kwargs.get("trigger_context"),
        )

    async def test_departure_off_honors_explicit_authority_without_erasing_manual(self):
        before = self.authority.record("light.living_room")
        result = await self.executor.async_execute(
            self._off(), "departure", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("completed", result["status"], result)
        self.assertTrue(result["receipts"][0]["confirmed"], result)
        self.assertEqual("off", self.hass.state_values["light.living_room"].state)
        self.assertEqual(before, self.authority.record("light.living_room"))
        self.hass.services.async_call.assert_awaited_once()

    async def test_departure_off_survives_two_nested_scenarios(self):
        definition = _definition((ScenarioAction(
            id="away", type=ScenarioActionType.RUN_SCENARIO, scenario_id="away",
        ),))
        result = await self.executor.async_execute(
            definition, "departure-nested", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("completed", result["status"], result)
        self.assertEqual("off", self.hass.state_values["light.living_room"].state)
        self.hass.services.async_call.assert_awaited_once()

    async def test_ordinary_absence_still_cannot_switch_off_manual_light(self):
        result = await self.executor.async_execute(
            self._off(), "absence", scenario_id="absence",
            trigger_context={"source": "device_state"},
        )
        self.assertTrue(result["receipts"][0]["skipped"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_departure_authority_does_not_authorize_turn_on(self):
        self._state("off")
        self.authority.capture("light.living_room", "off", "service", "manual-off")
        action = ScenarioAction(id="on", type=ScenarioActionType.DEVICE_ACTION,
                                target_id="device_1", action_id="turn_on")
        result = await self.executor._device_action_receipt(
            action, {"action_id": "on"}, automatic=True,
            automatic_light_off_authorized=True,
        )
        self.assertEqual("manual_light_priority", result.get("error"))
        self.hass.services.async_call.assert_not_awaited()

    async def test_departure_cannot_bypass_startup_uncertainty(self):
        self.authority.begin_startup_observation()
        result = await self.executor.async_execute(
            self._off(), "uncertain", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("failed", result["receipts"][0]["status"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_string_flag_does_not_grant_departure_authority(self):
        result = await self.executor.async_execute(
            self._off(), "forged", scenario_id="ordinary",
            trigger_context={"source": "nested", "departure_shutdown_authorized": "true"},
        )
        self.assertTrue(result["receipts"][0]["skipped"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_uncertain_authority_barriers_still_deny_departure_off(self):
        for barrier in ("storage", "selector", "graph", "return_pending"):
            with self.subTest(barrier=barrier):
                authority = ManualLightAuthority()
                if barrier == "storage":
                    authority.mark_unhealthy()
                elif barrier == "selector":
                    authority.mark_selector_uncertain("light")
                elif barrier == "graph":
                    authority.set_related_sources(RelatedLightSources(False, {}))
                else:
                    authority._explicit_return_pending = True
                executor = ScenarioExecutor(
                    self.hass, self.catalog, self._nested,
                    manual_light_authority=authority,
                )
                result = await executor.async_execute(
                    self._off(), "barrier-" + barrier, scenario_id="system-away-turn-off",
                    trigger_context={"source": "device_state"},
                )
                self.assertEqual("failed", result["receipts"][0]["status"])
                self.hass.services.async_call.assert_not_awaited()

    async def test_linked_unknown_record_is_not_hidden_by_known_own_record(self):
        room = SimpleNamespace(room_id="room", devices=SimpleNamespace(light_targets=(
            SimpleNamespace(entity_id="light.living_room"),
            SimpleNamespace(entity_id="switch.alternate"),
        )))
        self.authority.set_related_sources(RelatedLightSources.from_configs((room,)))
        self.authority.capture_legacy("switch.alternate", source="scenario")
        self.assertTrue(self.authority.blocks_departure_off("light.living_room"))
        result = await self.executor.async_execute(
            self._off(), "linked-unknown", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("failed", result["receipts"][0]["status"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_departure_keeps_writer_conflict_closed(self):
        self.executor._automatic_light_writer_conflict = lambda entity: True
        result = await self.executor.async_execute(
            self._off(), "conflict", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("light_writer_conflict", result["receipts"][0]["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_departure_unknown_readback_does_not_become_success(self):
        self._state("unavailable")

        async def no_report(*args, **kwargs):
            pass

        self.hass.services.async_call.side_effect = no_report
        result = await self.executor.async_execute(
            self._off(), "unknown", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertNotEqual("completed", result["status"])
        self.assertIsNot(True, result["receipts"][0].get("confirmed"))
        self.hass.services.async_call.assert_awaited_once()

    async def test_older_authority_without_departure_policy_stays_closed(self):
        self.executor._manual_light_authority = SimpleNamespace(blocks=lambda entity: True)
        result = await self.executor.async_execute(
            self._off(), "old-authority", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("manual_light_priority", result["receipts"][0]["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_zero_brightness_is_not_explicit_departure_off(self):
        action = ScenarioAction(id="zero", type=ScenarioActionType.DEVICE_ACTION,
                                target_id="device_1", action_id="set_brightness", value=0)
        result = await self.executor._device_action_receipt(
            action, {"action_id": "zero"}, automatic=True,
            automatic_light_off_authorized=True,
        )
        self.assertEqual("manual_light_priority", result.get("error"))
        self.hass.services.async_call.assert_not_awaited()

    async def test_unconfirmed_departure_stops_before_success_notification(self):
        self._state("unavailable")

        async def no_report(*args, **kwargs):
            pass

        self.hass.services.async_call.side_effect = no_report
        definition = _definition(self._off().actions + (ScenarioAction(
            id="announce", type=ScenarioActionType.NOTIFICATION, message="All lights off",
        ),))
        result = await self.executor.async_execute(
            definition, "no-false-success", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("state_not_confirmed", result["receipts"][0]["error"])
        self.assertEqual(1, len(result["receipts"]))
        self.hass.services.async_call.assert_awaited_once()

    async def test_nested_unknown_departure_propagates_failure(self):
        self._state("unavailable")

        async def no_report(*args, **kwargs):
            pass

        self.hass.services.async_call.side_effect = no_report
        definition = _definition((
            ScenarioAction(id="away", type=ScenarioActionType.RUN_SCENARIO, scenario_id="away"),
            ScenarioAction(id="announce", type=ScenarioActionType.NOTIFICATION, message="All off"),
        ))
        result = await self.executor.async_execute(
            definition, "nested-no-success", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("failed", result["status"])
        self.assertEqual("failed", result["receipts"][0]["nested_outcome"])
        self.assertEqual(1, len(result["receipts"]))
        self.hass.services.async_call.assert_awaited_once()

    async def test_departure_readback_exception_is_not_success_or_retry(self):
        from unittest.mock import AsyncMock

        self.executor._read_back_device = AsyncMock(side_effect=RuntimeError("no evidence"))
        result = await self.executor.async_execute(
            self._off(), "readback-error", scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state"},
        )
        self.assertEqual("failed", result["status"])
        self.assertFalse(result["receipts"][0]["confirmed"])
        self.assertEqual("state_not_confirmed", result["receipts"][0]["error"])
        self.hass.services.async_call.assert_awaited_once()
