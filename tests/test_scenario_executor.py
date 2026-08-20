"""Tests for the HausmanHub scenario executor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from custom_components.hausman_hub.application.scenario_executor import (
    ScenarioExecutor,
    _device_action_confirmed,
    _normalize_action_value,
    _number_range_error,
    _solar_curve_brightness,
    _value_parameter_name,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioDefinition,
    ScenarioDeviceCommand,
    ScenarioExecutionMode,
    ScenarioTrigger,
    ScenarioTriggerType,
)


class _FakeHass:
    def __init__(self) -> None:
        self.services = AsyncMock()
        self.states = SimpleNamespace(
            get=lambda entity_id: {
                "light.living_room": SimpleNamespace(state="on", attributes={}),
                "climate.living_room": SimpleNamespace(
                    state="cool", attributes={"temperature": 22}
                ),
                "number.breaker_temperature_threshold": SimpleNamespace(
                    state="80", attributes={}
                ),
                "sun.sun": SimpleNamespace(
                    state="below_horizon",
                    attributes={
                        "next_rising": "2026-08-15T00:00:00+00:00",
                        "next_setting": "2026-08-15T14:00:00+00:00",
                    },
                ),
                "cover.living_room": SimpleNamespace(state="closed", attributes={}),
            }.get(entity_id)
        )


class _FakeCatalog:
    def __init__(self) -> None:
        self._devices = {
            "device_1": ScenarioDeviceEntry(
                target_id="device_1",
                name="Light",
                entity_id="light.living_room",
                actions=(
                    ScenarioDeviceAction(
                        action_id="turn_on",
                        title="On",
                        domain="light",
                        service="turn_on",
                        allowed_fields=frozenset(),
                    ),
                    ScenarioDeviceAction(
                        action_id="set_brightness",
                        title="Brightness",
                        domain="light",
                        service="turn_on",
                        allowed_fields=frozenset({"value"}),
                    ),
                    ScenarioDeviceAction(
                        action_id="set_adaptive_brightness",
                        title="Adaptive brightness",
                        domain="light",
                        service="turn_on",
                        allowed_fields=frozenset({"value"}),
                    ),
                    ScenarioDeviceAction(
                        action_id="set_brightness_percent",
                        title="Яркость, %",
                        domain="light",
                        service="turn_on",
                        allowed_fields=frozenset({"value"}),
                    ),
                    ScenarioDeviceAction(
                        action_id="set_color_temperature",
                        title="Температура света",
                        domain="light",
                        service="turn_on",
                        allowed_fields=frozenset({"value"}),
                    ),
                ),
            ),
            "climate_1": ScenarioDeviceEntry(
                target_id="climate_1",
                name="Living room AC",
                entity_id="climate.living_room",
                actions=(
                    ScenarioDeviceAction(
                        action_id="turn_on",
                        title="On",
                        domain="climate",
                        service="turn_on",
                        allowed_fields=frozenset(),
                    ),
                    ScenarioDeviceAction(
                        action_id="set_temperature",
                        title="Temperature",
                        domain="climate",
                        service="set_temperature",
                        allowed_fields=frozenset({"value"}),
                    ),
                ),
            ),
            "number_1": ScenarioDeviceEntry(
                target_id="number_1",
                name="Порог отключения по температуре",
                entity_id="number.breaker_temperature_threshold",
                actions=(
                    ScenarioDeviceAction(
                        action_id="set_value",
                        title="Установить значение",
                        domain="number",
                        service="set_value",
                        allowed_fields=frozenset({"value"}),
                    ),
                ),
                range_minimum=40.0,
                range_maximum=100.0,
                range_step=1.0,
            ),
            "cover_1": ScenarioDeviceEntry(
                target_id="cover_1",
                name="Living room curtains",
                entity_id="cover.living_room",
                actions=(
                    ScenarioDeviceAction(
                        action_id="close_cover",
                        title="Close",
                        domain="cover",
                        service="close_cover",
                        allowed_fields=frozenset(),
                    ),
                ),
            ),
        }

    def device(self, target_id: str) -> Any | None:
        return self._devices.get(target_id)


def _definition(actions: tuple[ScenarioAction, ...]) -> ScenarioDefinition:
    return ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        triggers=(
            ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),
        ),
        conditions=(),
        actions=actions,
    )


class ScenarioExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.hass = _FakeHass()
        self.catalog = _FakeCatalog()
        self.nested_runs: list[str] = []

        async def run_callback(
            scenario_id: str, visited: frozenset[str] | None = None
        ) -> dict[str, Any]:
            self.nested_runs.append(scenario_id)
            return {
                "run_id": f"nested-{scenario_id}",
                "scenario_id": scenario_id,
                "status": "completed",
                "receipts": [],
            }

        self.executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            run_callback,
            notify_target="notify.mobile_app_tablet",
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

    async def test_device_action_calls_service(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="turn_on",
            ),
        ))
        result = await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["receipts"]), 1)
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room"}, blocking=True
        )

    async def test_closed_cover_is_not_commanded_again(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="cover_1",
                action_id="close_cover",
            ),
        ))

        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="close-curtains"
        )

        receipt = result["receipts"][0]
        self.assertEqual("completed", result["status"])
        self.assertTrue(result["confirmed"])
        self.assertTrue(receipt["skipped"])
        self.assertEqual("already_in_target_state", receipt["reason"])
        self.assertFalse(receipt["read_back"]["attempted"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_open_cover_still_receives_close_command(self) -> None:
        original_get = self.hass.states.get
        self.hass.states.get = lambda entity_id: (
            SimpleNamespace(state="open", attributes={})
            if entity_id == "cover.living_room"
            else original_get(entity_id)
        )
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="cover_1",
                action_id="close_cover",
            ),
        ))

        await self.executor.async_execute(
            definition, "run-1", scenario_id="close-curtains"
        )

        self.hass.services.async_call.assert_awaited_once_with(
            "cover",
            "close_cover",
            {"entity_id": "cover.living_room"},
            blocking=True,
        )

    async def test_unpowered_device_action_is_blocked_without_service_call(self) -> None:
        self.hass.states = SimpleNamespace(
            get=lambda entity_id: {
                "light.living_room": SimpleNamespace(state="on", attributes={}),
                "switch.wall": SimpleNamespace(state="off", attributes={}),
            }.get(entity_id)
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=lambda: {
                "light.living_room": "switch.wall"
            },
        )
        receipt = await executor.async_execute_device_action("device_1", "turn_on")
        self.assertFalse(receipt["accepted"])
        self.assertEqual("failed", receipt["status"])
        self.assertEqual("power_source_off", receipt["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_powered_device_action_uses_existing_executor_path(self) -> None:
        self.hass.states = SimpleNamespace(
            get=lambda entity_id: {
                "light.living_room": SimpleNamespace(state="on", attributes={}),
                "switch.wall": SimpleNamespace(state="on", attributes={}),
            }.get(entity_id)
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            power_dependency_resolver=lambda: {
                "light.living_room": "switch.wall"
            },
        )
        receipt = await executor.async_execute_device_action("device_1", "turn_on")
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once()

    async def test_scenario_uses_power_source_enabled_by_previous_action(self) -> None:
        self.catalog._devices["switch_1"] = ScenarioDeviceEntry(
            target_id="switch_1",
            name="Wall relay",
            entity_id="switch.wall",
            actions=(
                ScenarioDeviceAction(
                    action_id="turn_on",
                    title="On",
                    domain="switch",
                    service="turn_on",
                    allowed_fields=frozenset(),
                ),
            ),
        )
        self.hass.states = SimpleNamespace(
            get=lambda entity_id: {
                "light.living_room": SimpleNamespace(state="on", attributes={}),
                "switch.wall": SimpleNamespace(state="off", attributes={}),
                "sun.sun": SimpleNamespace(
                    state="below_horizon",
                    attributes={
                        "next_rising": "2026-08-15T06:00:00+06:00",
                        "next_setting": "2026-08-15T20:00:00+06:00",
                    },
                ),
            }.get(entity_id)
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=lambda: {
                "light.living_room": "switch.wall"
            },
        )
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="switch_1",
                action_id="turn_on",
            ),
            ScenarioAction(
                id="a2",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="set_adaptive_brightness",
                value=25,
            ),
        ))

        result = await executor.async_execute(
            definition,
            "run-1",
            scenario_id="sc-1",
            dry_run=True,
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", result["receipts"][1]["status"])
        self.assertEqual(25, result["receipts"][1]["adaptive_brightness"]["minimum_percent"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_scenario_confirms_device_actions_before_delay(self) -> None:
        self.executor._read_back_device = AsyncMock(
            return_value={
                "attempted": True,
                "matched": True,
                "observedAt": 1,
                "observedState": "on",
                "attempts": 1,
            }
        )
        readback_counts: list[int] = []

        async def fake_sleep(_: float) -> None:
            readback_counts.append(self.executor._read_back_device.await_count)

        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="turn_on",
            ),
            ScenarioAction(
                id="a2",
                type=ScenarioActionType.DELAY,
                delay_seconds=1,
            ),
            ScenarioAction(
                id="a3",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="turn_on",
            ),
        ))

        with patch(
            "custom_components.hausman_hub.application.scenario_executor.asyncio.sleep",
            side_effect=fake_sleep,
        ):
            result = await self.executor.async_execute(
                definition, "run-1", scenario_id="sc-1"
            )

        self.assertEqual("completed", result["status"])
        self.assertEqual([1], readback_counts)
        self.assertEqual(2, self.executor._read_back_device.await_count)

    async def test_scenario_confirms_multiple_devices_in_one_shared_window(self) -> None:
        both_readbacks_started = asyncio.Event()
        started = 0

        async def delayed_readback(
            entity_id: object, action_id: str, value: object | None
        ) -> dict[str, object]:
            nonlocal started
            self.assertEqual(2, self.hass.services.async_call.await_count)
            started += 1
            if started == 2:
                both_readbacks_started.set()
            await asyncio.wait_for(both_readbacks_started.wait(), timeout=0.1)
            return {
                "attempted": True,
                "matched": False,
                "observedAt": None,
                "observedState": "open",
                "attempts": 1,
            }

        self.executor._read_back_device = AsyncMock(side_effect=delayed_readback)
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="a2",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="sc-1"
        )

        self.assertEqual("completed", result["status"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(2, self.executor._read_back_device.await_count)
        self.assertTrue(
            all(
                receipt["reason"] == "state_not_confirmed"
                for receipt in result["receipts"]
            )
        )
        self.assertTrue(
            all(
                not any(key.startswith("_readback") for key in receipt)
                for receipt in result["receipts"]
            )
        )

    async def test_climate_action_uses_temperature_parameter(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="climate_1",
                action_id="set_temperature",
                value=22,
            ),
        ))
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "climate", "set_temperature", {"entity_id": "climate.living_room", "temperature": 22}, blocking=True
        )

    async def test_number_action_uses_selected_value_and_confirms_read_back(self) -> None:
        receipt = await self.executor.async_execute_device_action(
            "number_1", "set_value", 80
        )

        self.assertTrue(receipt["accepted"])
        self.assertEqual("confirmed", receipt["status"])
        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "number",
            "set_value",
            {
                "entity_id": "number.breaker_temperature_threshold",
                "value": 80.0,
            },
            blocking=True,
        )

    async def test_number_action_rejects_missing_or_out_of_range_value(self) -> None:
        missing = await self.executor.async_execute_device_action(
            "number_1", "set_value"
        )
        outside = await self.executor.async_execute_device_action(
            "number_1", "set_value", 101
        )

        self.assertEqual("failed", missing["status"])
        self.assertEqual("value is required for a numeric control", missing["error"])
        self.assertEqual("failed", outside["status"])
        self.assertEqual("value is outside the allowed range", outside["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_action_with_value_uses_brightness_parameter(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="set_brightness",
                value=128,
            ),
        ))
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room", "brightness": 128}, blocking=True
        )

    async def test_normalized_brightness_value_is_confirmed_against_read_back(self) -> None:
        self.hass.states.get = lambda entity_id: SimpleNamespace(
            state="on", attributes={"brightness": 50}
        )

        receipt = await self.executor.async_execute_device_action(
            "device_1", "set_brightness", "50%"
        )

        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room", "brightness": 50}, blocking=True
        )

    async def test_brightness_percent_scales_to_native_brightness(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="set_brightness_percent",
                value=50,
            ),
        ))
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room", "brightness": 128}, blocking=True
        )

    async def test_brightness_percent_is_confirmed_against_read_back(self) -> None:
        self.hass.states.get = lambda entity_id: SimpleNamespace(
            state="on", attributes={"brightness": 128}
        )

        receipt = await self.executor.async_execute_device_action(
            "device_1", "set_brightness_percent", "50"
        )

        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room", "brightness": 128}, blocking=True
        )

    async def test_color_temperature_action_uses_kelvin_parameter(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="set_color_temperature",
                value=3000,
            ),
        ))
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "color_temp_kelvin": 3000},
            blocking=True,
        )

    async def test_color_temperature_readback_tolerates_mired_rounding(self) -> None:
        self.hass.states.get = lambda entity_id: SimpleNamespace(
            state="on", attributes={"color_temp_kelvin": 3003}
        )

        receipt = await self.executor.async_execute_device_action(
            "device_1", "set_color_temperature", 3000
        )

        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "color_temp_kelvin": 3000},
            blocking=True,
        )

    async def test_adaptive_brightness_uses_solar_curve_and_minimum_percent(self) -> None:
        self.hass.states.get = lambda entity_id: {
            "light.living_room": SimpleNamespace(
                state="on", attributes={"brightness": 159}
            ),
            "sun.sun": SimpleNamespace(
                state="below_horizon",
                attributes={
                    "next_rising": "2026-08-15T06:00:00+06:00",
                    "next_setting": "2026-08-15T20:00:00+06:00",
                },
            ),
        }.get(entity_id)
        now = datetime(
            2026, 8, 14, 22, 0, tzinfo=timezone(timedelta(hours=6))
        )

        with patch(
            "custom_components.hausman_hub.application.scenario_executor._now_local",
            return_value=now,
        ):
            receipt = await self.executor.async_execute_device_action(
                "device_1", "set_adaptive_brightness", 25
            )

        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "brightness": 159},
            blocking=True,
        )

    def test_solar_curve_is_darkest_at_midnight_and_reverses_in_morning(self) -> None:
        setting = "2026-08-15T20:00:00+06:00"
        rising = "2026-08-15T06:00:00+06:00"
        tz = timezone(timedelta(hours=6))
        self.assertEqual(
            255,
            _solar_curve_brightness(
                datetime(2026, 8, 14, 20, 0, tzinfo=tz),
                "below_horizon",
                rising,
                setting,
                25,
            ),
        )
        self.assertEqual(
            64,
            _solar_curve_brightness(
                datetime(2026, 8, 15, 0, 0, tzinfo=tz),
                "below_horizon",
                rising,
                setting,
                25,
            ),
        )
        self.assertEqual(
            159,
            _solar_curve_brightness(
                datetime(2026, 8, 15, 3, 0, tzinfo=tz),
                "below_horizon",
                rising,
                setting,
                25,
            ),
        )

    async def test_delay_action_receipt(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DELAY,
                delay_seconds=1,
            ),
        ))
        result = await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["receipts"][0]["status"], "completed")
        self.assertEqual(result["receipts"][0]["delay_seconds"], 1)

    async def test_run_scenario_action_calls_callback(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.RUN_SCENARIO,
                scenario_id="other",
            ),
        ))
        result = await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.nested_runs, ["other"])
        self.assertEqual(result["receipts"][0]["nested_run_id"], "nested-other")

    async def test_run_scenario_dry_run_is_a_successful_plan(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.RUN_SCENARIO,
                scenario_id="other",
            ),
        ))

        result = await self.executor.async_execute(
            definition,
            "run-1",
            scenario_id="sc-1",
            dry_run=True,
        )

        self.assertEqual("completed", result["status"])
        self.assertTrue(result["receipts"][0]["planned"])
        self.assertEqual([], self.nested_runs)

    async def test_notification_action_targets_configured_entity(self) -> None:
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.NOTIFICATION,
                message="Hello",
            ),
        ))
        result = await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "completed")
        self.hass.services.async_call.assert_awaited_once_with(
            "notify",
            "mobile_app_tablet",
            {"message": "Hello", "data": {"correlation_id": "run-1"}},
            blocking=True,
        )
        self.assertEqual("run-1", result["receipts"][0]["correlation_id"])

    async def test_notification_fails_without_target(self) -> None:
        executor = ScenarioExecutor(self.hass, self.catalog, self.executor._run_callback)
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.NOTIFICATION,
                message="Hello",
            ),
        ))
        result = await executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "failed")
        self.assertIn("notification target is not configured", result["receipts"][0]["error"])

    async def test_failed_service_stops_execution(self) -> None:
        self.hass.services.async_call.side_effect = RuntimeError("boom")
        definition = _definition((
            ScenarioAction(
                id="a1",
                type=ScenarioActionType.DEVICE_ACTION,
                target_id="device_1",
                action_id="turn_on",
            ),
            ScenarioAction(id="a2", type=ScenarioActionType.NOTIFICATION, message="x"),
        ))
        result = await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(result["receipts"]), 1)
        self.assertEqual(result["receipts"][0]["status"], "failed")
        self.assertIn("boom", result["receipts"][0]["error"])

    async def test_new_run_id_unique(self) -> None:
        run_id_1 = self.executor.new_run_id()
        run_id_2 = self.executor.new_run_id()
        self.assertNotEqual(run_id_1, run_id_2)
        self.assertEqual(len(run_id_1), 32)

    async def test_public_device_action_returns_confirmed_read_back(self) -> None:
        receipt = await self.executor.async_execute_device_action(
            "device_1", "turn_on"
        )

        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["confirmed"])
        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual("on", receipt["observedState"])
        # Решение владельца 2026-08-20: квитанция называет устройство по имени.
        self.assertEqual("Light: новое состояние подтверждено.", receipt["message"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room"}, blocking=True
        )

    async def test_public_device_action_rejects_unknown_target(self) -> None:
        receipt = await self.executor.async_execute_device_action(
            "missing", "turn_on"
        )

        self.assertFalse(receipt["accepted"])
        self.assertFalse(receipt["confirmed"])
        self.assertEqual("failed", receipt["status"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_public_device_action_reports_bounded_unconfirmed_read_back(self) -> None:
        self.hass.states.get = lambda entity_id: SimpleNamespace(
            state="off", attributes={}
        )

        receipt = await self.executor.async_execute_device_action(
            "device_1", "turn_on"
        )

        self.assertTrue(receipt["accepted"])
        self.assertFalse(receipt["confirmed"])
        self.assertEqual("accepted", receipt["status"])
        self.assertEqual("Проверяется", receipt["statusName"])
        self.assertEqual(
            "Light: команда принята, состояние ещё не подтверждено.",
            receipt["message"],
        )
        self.assertEqual(20, receipt["confirmationWindowMs"])
        self.assertTrue(receipt["readBack"]["attempted"])
        self.assertFalse(receipt["readBack"]["matched"])
        self.assertEqual("state_not_confirmed", receipt["reason"])

    def test_extended_value_actions_use_typed_ha_parameters(self) -> None:
        self.assertEqual(
            "humidity",
            _value_parameter_name("set_humidity", "humidifier", "set_humidity"),
        )
        self.assertEqual(
            "position",
            _value_parameter_name("set_position", "valve", "set_valve_position"),
        )
        self.assertEqual(45, _normalize_action_value("humidity", "45%"))
        self.assertEqual(
            "value",
            _value_parameter_name("set_value", "number", "set_value"),
        )
        self.assertEqual(80.0, _normalize_action_value("value", "80"))

    def test_number_action_rejects_out_of_range_and_off_step_values(self) -> None:
        device = SimpleNamespace(
            range_minimum=40.0,
            range_maximum=100.0,
            range_step=1.0,
        )
        self.assertIsNone(_number_range_error(device, 80.0))
        self.assertEqual(
            "value is outside the allowed range",
            _number_range_error(device, 101.0),
        )
        device.range_step = 0.5
        self.assertEqual(
            "value does not match the allowed step",
            _number_range_error(device, 80.25),
        )

    def test_extended_actions_have_explicit_read_back_rules(self) -> None:
        self.assertTrue(
            _device_action_confirmed(
                SimpleNamespace(state="locked", attributes={}), "lock", None
            )
        )
        self.assertTrue(
            _device_action_confirmed(
                SimpleNamespace(state="cleaning", attributes={}), "start", None
            )
        )
        self.assertTrue(
            _device_action_confirmed(
                SimpleNamespace(state="open", attributes={}), "open_valve", None
            )
        )
        self.assertTrue(
            _device_action_confirmed(
                SimpleNamespace(state="80", attributes={}), "set_value", 80.0
            )
        )


if __name__ == "__main__":
    unittest.main()
