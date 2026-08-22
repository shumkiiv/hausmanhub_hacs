from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.hausman_hub.application.water_safety import (
    WaterSafetyService,
    default_water_safety_configuration,
    validate_water_safety_configuration,
)


class MemoryStore:
    def __init__(self, payload: object | None = None) -> None:
        self.payload = payload
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> object | None:
        return self.payload

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.saved.append(payload)


class FakeStates:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get(self, entity_id: str) -> object | None:
        value = self.values.get(entity_id)
        if value is None:
            return None
        return SimpleNamespace(
            state=value,
            attributes={},
            last_updated=datetime(2026, 8, 22, 9, 0, tzinfo=UTC),
        )


class FakeServices:
    def __init__(self, states: FakeStates) -> None:
        self.states = states
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def has_service(self, target: str) -> bool:
        domain, service = target.split(".", 1)
        return (domain, service) in {
            ("notify", "mobile_app_tablet"),
            ("switch", "turn_off"),
        }

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict[str, object],
        *,
        blocking: bool,
    ) -> None:
        del blocking
        self.calls.append((domain, service, data))
        if domain == "switch" and service == "turn_off":
            self.states.values[str(data["entity_id"])] = "off"

    async def async_close(self, entity_id: str, close_action: str) -> None:
        await self.async_call(
            entity_id.split(".", 1)[0],
            close_action,
            {"entity_id": entity_id},
            blocking=True,
        )

    async def async_notify(self, target: str, message: str) -> None:
        domain, service = target.split(".", 1)
        await self.async_call(
            domain,
            service,
            {"message": message},
            blocking=True,
        )


class FakeJournal:
    def __init__(self) -> None:
        self.receipts: list[dict[str, object]] = []

    async def async_append(self, receipt: dict[str, object]) -> None:
        self.receipts.append(receipt)


def configured_policy(*, auto_close: bool = True, quorum: int = 1) -> dict[str, object]:
    return {
        "enabled": True,
        "sensorEntityIds": ["binary_sensor.leak_a", "binary_sensor.leak_b"],
        "actuators": [
            {
                "entityId": "switch.cold_water",
                "closeAction": "turn_off",
                "openStates": ["on"],
                "closedStates": ["off"],
            },
            {
                "entityId": "switch.hot_water",
                "closeAction": "turn_off",
                "openStates": ["on"],
                "closedStates": ["off"],
            },
        ],
        "requiredActiveSensors": quorum,
        "activationDebounceSeconds": 3,
        "clearDebounceSeconds": 30,
        "recipientServices": ["notify.mobile_app_tablet"],
        "directionVerified": True,
        "autoCloseEnabled": auto_close,
    }


class WaterSafetyServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.states = FakeStates(
            {
                "binary_sensor.leak_a": "off",
                "binary_sensor.leak_b": "off",
                "switch.cold_water": "on",
                "switch.hot_water": "on",
            }
        )
        self.hass = SimpleNamespace(states=self.states)
        self.services = FakeServices(self.states)
        self.hass.services = self.services
        self.store = MemoryStore()
        self.journal = FakeJournal()
        self.service = WaterSafetyService(
            self.hass,
            self.store,
            command_gateway=self.services,
            operation_journal=self.journal,
            now_ms=lambda: 1787389200000,
            readback_window_seconds=0.05,
            readback_interval_seconds=0.01,
        )
        await self.service.async_load()

    async def test_default_policy_is_disabled_and_sends_nothing(self) -> None:
        snapshot = self.service.snapshot()

        self.assertEqual(default_water_safety_configuration(), snapshot["configuration"])
        self.assertFalse(snapshot["state"]["automaticOpenAllowed"])
        self.assertEqual([], self.services.calls)

    async def test_direction_test_is_strictly_read_only(self) -> None:
        await self.service.async_update(0, configured_policy(auto_close=False))

        receipt = self.service.direction_test("switch.cold_water")

        self.assertFalse(receipt["commandSent"])
        self.assertEqual("open", receipt["readBack"])
        self.assertTrue(receipt["safeToConfirm"])
        self.assertEqual([], self.services.calls)

    async def test_quorum_closes_both_actuators_and_latches(self) -> None:
        await self.service.async_update(0, configured_policy(quorum=2))
        self.states.values["binary_sensor.leak_a"] = "on"
        await self.service._async_activate()
        self.assertFalse(self.service.snapshot()["state"]["latched"])

        self.states.values["binary_sensor.leak_b"] = "on"
        await self.service._async_activate()

        state = self.service.snapshot()["state"]
        self.assertTrue(state["latched"])
        self.assertEqual("closed", state["valveState"])
        self.assertEqual("confirmed", state["commandStatus"])
        physical = [call for call in self.services.calls if call[0] == "switch"]
        self.assertEqual(2, len(physical))
        self.assertEqual(1, len(self.journal.receipts))
        self.assertTrue(self.journal.receipts[0]["confirmed"])

    async def test_latch_survives_restart_and_blocks_manual_open(self) -> None:
        await self.service.async_update(0, configured_policy())
        self.states.values["binary_sensor.leak_a"] = "on"
        await self.service._async_activate()
        restarted = WaterSafetyService(self.hass, self.store)
        await restarted.async_load()

        self.assertEqual(
            "water_leak_latched",
            restarted.command_guard("switch.cold_water", "turn_on", False),
        )
        self.assertEqual(
            "automatic_water_open_forbidden",
            restarted.command_guard("switch.cold_water", "turn_on", True),
        )
        self.assertIsNone(
            restarted.command_guard("switch.cold_water", "turn_off", True)
        )

    async def test_restart_reconfirms_latched_close_even_after_sensor_clears(self) -> None:
        await self.service.async_update(0, configured_policy())
        self.states.values["binary_sensor.leak_a"] = "on"
        await self.service._async_activate()
        self.states.values["binary_sensor.leak_a"] = "off"
        self.states.values["switch.cold_water"] = "on"
        self.states.values["switch.hot_water"] = "on"
        calls_before_restart = len(self.services.calls)

        restarted = WaterSafetyService(
            self.hass,
            self.store,
            command_gateway=self.services,
            operation_journal=self.journal,
            readback_window_seconds=0.05,
            readback_interval_seconds=0.01,
        )
        await restarted.async_load()
        restarted.start()
        await asyncio.sleep(0.02)

        self.assertEqual("closed", restarted.snapshot()["state"]["valveState"])
        self.assertEqual("confirmed", restarted.snapshot()["state"]["commandStatus"])
        recovery_calls = self.services.calls[calls_before_restart:]
        self.assertEqual(2, len([call for call in recovery_calls if call[0] == "switch"]))

    async def test_unverified_state_blocks_manual_open_and_latch_clear(self) -> None:
        await self.service.async_update(0, configured_policy(auto_close=False))
        self.states.values["binary_sensor.leak_a"] = "unavailable"

        self.assertEqual(
            "water_safety_state_unverified",
            self.service.command_guard("switch.cold_water", "turn_on", False),
        )
        self.service._latched = True
        with self.assertRaisesRegex(ValueError, "active leak"):
            await self.service.async_clear_latch(1, confirmation=True)

    async def test_clear_latch_requires_dry_sensors_and_explicit_confirmation(self) -> None:
        await self.service.async_update(0, configured_policy())
        self.states.values["binary_sensor.leak_a"] = "on"
        await self.service._async_activate()
        with self.assertRaisesRegex(ValueError, "active leak"):
            await self.service.async_clear_latch(2, confirmation=True)

        self.states.values["binary_sensor.leak_a"] = "off"
        with self.assertRaisesRegex(ValueError, "confirmation"):
            await self.service.async_clear_latch(2, confirmation=False)
        with self.assertRaisesRegex(ValueError, "active leak"):
            await self.service.async_clear_latch(2, confirmation=True)
        await self.service._async_finish_clear_debounce()
        snapshot = await self.service.async_clear_latch(2, confirmation=True)

        self.assertFalse(snapshot["state"]["latched"])
        self.assertFalse(snapshot["state"]["automaticOpenAllowed"])
        self.assertEqual("water_safety_latch_clear", self.journal.receipts[-1]["operation"])
        self.assertTrue(self.journal.receipts[-1]["confirmed"])

    def test_auto_close_configuration_fails_closed_without_prerequisites(self) -> None:
        policy = configured_policy()
        policy["recipientServices"] = []
        with self.assertRaisesRegex(ValueError, "auto close requires"):
            validate_water_safety_configuration(policy)

    def test_configuration_rejects_ids_outside_contract(self) -> None:
        policy = configured_policy(auto_close=False)
        policy["sensorEntityIds"] = ["Binary_Sensor.Leak"]
        with self.assertRaisesRegex(ValueError, "sensor entityId"):
            validate_water_safety_configuration(policy)

        policy = configured_policy(auto_close=False)
        policy["recipientServices"] = ["notify.Mobile_App"]
        with self.assertRaisesRegex(ValueError, "recipient service"):
            validate_water_safety_configuration(policy)


if __name__ == "__main__":
    unittest.main()
