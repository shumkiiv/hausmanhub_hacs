"""Tests for the HausmanHub scenario executor."""

from __future__ import annotations

import asyncio
import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, call, patch

from custom_components.hausman_hub.application.scenario_executor import (
    ScenarioExecutor,
    _device_action_confirmed,
    _display_device_name,
    _normalize_action_value,
    _normalize_light_action_value,
    _number_range_error,
    _solar_curve_brightness,
    _value_parameter_name,
)
from custom_components.hausman_hub.application.operation_journal import (
    scenario_operation_receipt,
)
from custom_components.hausman_hub.application.manual_light_off_protection import (
    ManualLightOffProtectionCoordinator,
)
from custom_components.hausman_hub.domain.manual_light_off_protection import (
    LightProtectionDecision,
)
from custom_components.hausman_hub.application.device_action_receipts import (
    evidence_snapshot,
)
from custom_components.hausman_hub.application.scenario_light_priority import (
    LightAutomationPriority,
)
from custom_components.hausman_hub.application.scenario_command_context import (
    ScenarioCommandContextRegistry,
)
from custom_components.hausman_hub.manual_light_off_protection_events import (
    ManualLightOffProtectionEventListener,
)
from custom_components.hausman_hub.application.light_safety_obligations import (
    RECONCILE_INVALIDATED,
    LightSafetyObligations,
)
from custom_components.hausman_hub.application.scenarios import (
    ScenarioCatalog,
    ScenarioDeviceAction,
    ScenarioDeviceEntry,
)
from custom_components.hausman_hub.application.vendor_resilience import (
    VendorCircuitBreaker,
)
from custom_components.hausman_hub.domain.device_power_dependencies import (
    DevicePowerDependency,
)
from custom_components.hausman_hub.domain.scenarios import (
    ScenarioAction,
    ScenarioActionType,
    ScenarioCommandMode,
    ScenarioComparison,
    ScenarioCondition,
    ScenarioConditionType,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedMetadata,
    ScenarioSafetyPolicy,
    ScenarioTrigger,
    ScenarioTriggerType,
)


def _power_link(
    *, policy: str = "requires_on", warmup_seconds: int = 0
) -> dict[str, DevicePowerDependency]:
    return {
        "light.living_room": DevicePowerDependency(
            "light.living_room",
            "switch.wall",
            policy,
            warmup_seconds,
        )
    }


class _FakeHass:
    def __init__(self) -> None:
        self.services = AsyncMock()
        self.state_values = {
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
            "valve.main": SimpleNamespace(state="open", attributes={}),
            "media_player.living_room": SimpleNamespace(state="off", attributes={}),
            "binary_sensor.hall_motion": SimpleNamespace(state="on", attributes={}),
            "binary_sensor.shower_presence": SimpleNamespace(state="off", attributes={}),
            "switch.shower_fan": SimpleNamespace(state="on", attributes={}),
        }
        self.states = SimpleNamespace(get=self.state_values.get)


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
                        action_id="turn_off",
                        title="Off",
                        domain="light",
                        service="turn_off",
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
                    ScenarioDeviceAction(
                        action_id="set_night_light",
                        title="Ночной свет",
                        domain="light",
                        service="turn_on",
                        allowed_fields=frozenset({"value"}),
                    ),
                    ScenarioDeviceAction(
                        action_id="set_rgb_color",
                        title="Цвет",
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
            "media_1": ScenarioDeviceEntry(
                target_id="media_1",
                name="Living room TV",
                entity_id="media_player.living_room",
                actions=(
                    ScenarioDeviceAction(
                        action_id="turn_on",
                        title="On",
                        domain="media_player",
                        service="turn_on",
                        allowed_fields=frozenset(),
                    ),
                ),
            ),
            "valve_1": ScenarioDeviceEntry(
                target_id="valve_1",
                name="Main valve",
                entity_id="valve.main",
                actions=(
                    ScenarioDeviceAction(
                        action_id="close_valve",
                        title="Close",
                        domain="valve",
                        service="close_valve",
                        allowed_fields=frozenset(),
                    ),
                ),
            ),
            "sensor_1": ScenarioDeviceEntry(
                target_id="sensor_1",
                name="Датчик движения",
                entity_id="binary_sensor.hall_motion",
                actions=(),
            ),
            "fan_1": ScenarioDeviceEntry(
                target_id="fan_1",
                name="Вытяжка душевой",
                entity_id="switch.shower_fan",
                actions=(
                    ScenarioDeviceAction(
                        action_id="turn_on",
                        title="On",
                        domain="switch",
                        service="turn_on",
                        allowed_fields=frozenset(),
                    ),
                    ScenarioDeviceAction(
                        action_id="turn_off",
                        title="Off",
                        domain="switch",
                        service="turn_off",
                        allowed_fields=frozenset(),
                    ),
                ),
            ),
        }

    def device(self, target_id: str) -> Any | None:
        return self._devices.get(target_id)


def _definition(
    actions: tuple[ScenarioAction, ...],
    *,
    idempotent_actions: bool = False,
    command_mode: ScenarioCommandMode = ScenarioCommandMode.LIVE,
) -> ScenarioDefinition:
    return ScenarioDefinition(
        version=1,
        execution_mode=ScenarioExecutionMode.SINGLE,
        command_mode=command_mode,
        triggers=(ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),),
        conditions=(),
        actions=actions,
        safety_policy=ScenarioSafetyPolicy(idempotent_actions=idempotent_actions),
    )


class ScenarioExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.hass = _FakeHass()
        self.catalog = _FakeCatalog()
        self.nested_runs: list[str] = []

        async def run_callback(
            scenario_id: str,
            visited: frozenset[str] | None = None,
            **_kwargs: object,
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

    async def test_manual_off_protection_blocks_automatic_light_before_power_preparation(
        self,
    ) -> None:
        """A protected automatic activation must not energize its relay first."""

        order: list[str] = []

        class Store:
            payload: object | None = None

            async def async_load(self) -> object | None:
                return self.payload

            async def async_save(self, payload: dict[str, object]) -> None:
                self.payload = payload

        class BlockingProtection(ManualLightOffProtectionCoordinator):
            async def async_decide(self, action, catalog, *, automatic, dry_run, trigger_context):
                order.append("protection")
                return await super().async_decide(
                    action,
                    catalog,
                    automatic=automatic,
                    dry_run=dry_run,
                    trigger_context=trigger_context,
                )

        async def unexpected_power(*_args: object, **_kwargs: object):
            raise AssertionError("power preparation must follow protection")

        protection = BlockingProtection(Store())
        await protection.async_load()
        await protection.async_replace_settings(
            "settings.1",
            0,
            {
                "globalPolicy": {
                    "enabled": True,
                    "minimumIntervalSeconds": 600,
                    "releaseMode": "timer_only",
                    "stableAbsenceSeconds": 30,
                    "extendOnRepeatedManualOff": True,
                    "noSensorFallback": "timer_only",
                    "protectedScope": "profile",
                    "allowManualRelease": True,
                },
                "roomOverrides": {},
                "profileOverrides": {},
                "profiles": [{
                    "roomId": "living_room",
                    "profileId": "living_room_light",
                    "lightIds": ["light.living_room"],
                    "presenceSensorIds": [],
                }],
            },
        )
        await protection.async_note_state_transition(
            "light.living_room",
            SimpleNamespace(state="on"),
            SimpleNamespace(state="off"),
            None,
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=lambda: _power_link(policy="auto_turn_on"),
            manual_light_off_protection=protection,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off", attributes={}
        )
        self.hass.state_values["switch.wall"] = SimpleNamespace(state="off", attributes={})
        executor._prepare_power_dependency = unexpected_power  # type: ignore[method-assign]

        result = await executor.async_execute(
            _definition(
                (
                    ScenarioAction(
                        id="protected_on",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id="device_1",
                        action_id="turn_on",
                    ),
                )
            ),
            "run-protected-on",
            scenario_id="presence_light",
            trigger_context={"source": "device_state", "target_id": "sensor_1"},
        )

        self.assertEqual(["protection", "protection"], order)
        self.assertEqual("completed", result["status"])
        self.assertEqual("manual_off_protection_active", result["receipts"][0]["reason"])
        self.assertFalse(result["receipts"][0]["physicalAttempted"])
        self.assertEqual("living_room_light", result["receipts"][0]["protection"]["profileId"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_manual_off_protection_uses_light_role_for_switch_relay(self) -> None:
        class BlockingProtection:
            async def async_decide(self, action, catalog, **_kwargs):
                device = catalog.device(action.target_id)
                return LightProtectionDecision(
                    device.entity_id != "switch.hall_light",
                    "manual_off_protection_active",
                    "protection-1",
                    {"lightIds": ["switch.hall_light"]},
                )

        self.catalog._devices["relay_light"] = ScenarioDeviceEntry(
            target_id="relay_light",
            name="Световой relay",
            entity_id="switch.hall_light",
            actions=(ScenarioDeviceAction("turn_on", "On", "switch", "turn_on", frozenset()),),
        )
        self.catalog._devices["outlet"] = ScenarioDeviceEntry(
            target_id="outlet",
            name="Розетка",
            entity_id="switch.outlet",
            actions=(ScenarioDeviceAction("turn_on", "On", "switch", "turn_on", frozenset()),),
        )
        self.hass.state_values["switch.hall_light"] = SimpleNamespace(state="off", attributes={})
        self.hass.state_values["switch.outlet"] = SimpleNamespace(state="off", attributes={})
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            manual_light_off_protection=BlockingProtection(),
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        protected = await executor.async_execute(
            _definition((ScenarioAction(id="relay_on", type=ScenarioActionType.DEVICE_ACTION, target_id="relay_light", action_id="turn_on"),)),
            "switch-guard.1",
            scenario_id="hall_light",
            scenario_title="Свет",
        )

        self.assertEqual("manual_off_protection_active", protected["receipts"][0]["reason"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_protected_source_substitution_skips_earlier_automatic_off(self) -> None:
        class BlockingProtection:
            async def async_decide(self, action, catalog, **_kwargs):
                device = catalog.device(action.target_id)
                return LightProtectionDecision(
                    device.entity_id != "light.alt", "manual_off_protection_active", "protection-1",
                    {"lightIds": ["light.living_room", "light.alt"]},
                )

        self.catalog._devices["device_alt"] = ScenarioDeviceEntry(
            target_id="device_alt",
            name="Alternate light",
            entity_id="light.alt",
            actions=(ScenarioDeviceAction("turn_on", "On", "light", "turn_on", frozenset()),),
        )
        self.hass.state_values["light.alt"] = SimpleNamespace(state="off", attributes={})
        priority = LightAutomationPriority()
        priority._owned_revisions["light.living_room"] = None  # noqa: SLF001
        priority._owned_records["light.living_room"] = {"expiresAt": 9_999_999_999_999}  # noqa: SLF001
        executor = ScenarioExecutor(
            self.hass, self.catalog, self.executor._run_callback,
            light_priority=priority, manual_light_off_protection=BlockingProtection(),
            readback_window_seconds=0.02, readback_interval_seconds=0.01,
        )

        result = await executor.async_execute(
            _definition((
                ScenarioAction(id="old_off", type=ScenarioActionType.DEVICE_ACTION, target_id="device_1", action_id="turn_off"),
                ScenarioAction(id="new_on", type=ScenarioActionType.DEVICE_ACTION, target_id="device_alt", action_id="turn_on"),
            )),
            "substitution-guard.1", scenario_id="source_substitution",
        )

        self.assertEqual(["manual_off_protection_active"] * 2, [item["reason"] for item in result["receipts"]])
        self.hass.services.async_call.assert_not_awaited()

    async def test_substitution_rechecks_protection_under_one_authority_lock(self) -> None:
        """A manual off after discovery blocks the whole source substitution."""

        class PausedDiscoveryProtection:
            def __init__(self) -> None:
                self.discovery_complete = asyncio.Event()
                self.manual_off_arrived = asyncio.Event()

            async def async_decide(self, action, catalog, *, dry_run, **_kwargs):
                if dry_run:
                    self.discovery_complete.set()
                    await self.manual_off_arrived.wait()
                    return LightProtectionDecision(True)
                device = catalog.device(action.target_id)
                return LightProtectionDecision(
                    device.entity_id != "light.alt",
                    "manual_off_protection_active",
                    "protection-1",
                    {"lightIds": ["light.living_room", "light.alt"]},
                )

        self.catalog._devices["device_alt"] = ScenarioDeviceEntry(
            target_id="device_alt",
            name="Alternate light",
            entity_id="light.alt",
            actions=(ScenarioDeviceAction("turn_on", "On", "light", "turn_on", frozenset()),),
        )
        self.hass.state_values["light.alt"] = SimpleNamespace(state="off", attributes={})
        priority = LightAutomationPriority()
        priority._owned_revisions["light.living_room"] = None  # noqa: SLF001
        priority._owned_records["light.living_room"] = {"expiresAt": 9_999_999_999_999}  # noqa: SLF001
        protection = PausedDiscoveryProtection()
        executor = ScenarioExecutor(
            self.hass, self.catalog, self.executor._run_callback,
            light_priority=priority, manual_light_off_protection=protection,
            readback_window_seconds=0.02, readback_interval_seconds=0.01,
        )
        run = asyncio.create_task(executor.async_execute(
            _definition((
                ScenarioAction(id="old_off", type=ScenarioActionType.DEVICE_ACTION, target_id="device_1", action_id="turn_off"),
                ScenarioAction(id="new_on", type=ScenarioActionType.DEVICE_ACTION, target_id="device_alt", action_id="turn_on"),
            )),
            "substitution-race.1", scenario_id="source_substitution",
        ))
        await protection.discovery_complete.wait()
        protection.manual_off_arrived.set()
        result = await run

        self.assertEqual(["manual_off_protection_active"] * 2, [item["reason"] for item in result["receipts"]])
        self.hass.services.async_call.assert_not_awaited()

    async def test_device_action_calls_service(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            )
        )
        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="sc-1"
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["receipts"]), 1)
        self.hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on", {"entity_id": "light.living_room"}, blocking=True
        )

    async def test_automatic_light_off_registers_context_for_state_attribution(self) -> None:
        """Removing context registration would make automatic off look manual."""

        contexts = ScenarioCommandContextRegistry(
            context_factory=lambda: SimpleNamespace(id="automatic.light.off")
        )
        priority = LightAutomationPriority()
        priority._owned_revisions["light.living_room"] = None  # noqa: SLF001
        priority._owned_records["light.living_room"] = {  # noqa: SLF001
            "expiresAt": 9_999_999_999_999,
        }
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_priority=priority,
            command_contexts=contexts,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="automatic_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        await executor.async_execute(
            definition,
            "automatic-off.1",
            scenario_id="presence_off",
            trigger_context={"source": "device_state", "target_id": "sensor_1"},
        )

        context = self.hass.services.async_call.await_args.kwargs["context"]
        protected = SimpleNamespace(async_note_state_transition=AsyncMock())
        listener = ManualLightOffProtectionEventListener(
            protected, contexts, {"light.living_room"}
        )
        await listener.async_handle(
            SimpleNamespace(
                data={
                    "entity_id": "light.living_room",
                    "old_state": SimpleNamespace(
                        state="on",
                        last_updated=datetime.now(timezone.utc),
                        attributes={},
                    ),
                    "new_state": SimpleNamespace(
                        state="off",
                        last_updated=datetime.now(timezone.utc),
                        attributes={},
                    ),
                },
                context=context,
            )
        )

        attribution = protected.async_note_state_transition.await_args.args[3]
        self.assertEqual("automatic", attribution.source)

    async def test_manual_authority_is_persisted_and_obligation_cancelled_before_dispatch(
        self,
    ) -> None:
        order: list[str] = []
        priority = LightAutomationPriority()

        async def prepare(*_args: object) -> dict[str, object]:
            order.append("persist_manual")
            return {"targetId": "device_1", "previous": None}

        async def cancel(_target_id: str) -> None:
            order.append("cancel_obligation")

        async def dispatch(*_args: object, **_kwargs: object) -> None:
            order.append("dispatch")

        priority._async_begin_direct_action_unlocked = AsyncMock(side_effect=prepare)
        obligations = AsyncMock()
        obligations.async_cancel.side_effect = cancel
        self.hass.services.async_call.side_effect = dispatch
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_priority=priority,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertTrue(receipt["accepted"])
        self.assertEqual(
            ["persist_manual", "cancel_obligation", "dispatch"], order
        )

    async def test_manual_authority_storage_failure_blocks_physical_dispatch(
        self,
    ) -> None:
        priority = LightAutomationPriority()
        priority._async_begin_direct_action_unlocked = AsyncMock(
            side_effect=OSError("store unavailable")
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_priority=priority,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        with self.assertRaisesRegex(OSError, "store unavailable"):
            await executor.async_execute_device_action("device_1", "turn_on")

        self.hass.services.async_call.assert_not_awaited()

    async def test_failed_manual_dispatch_rolls_back_authority(self) -> None:
        priority = LightAutomationPriority()
        self.hass.services.async_call.side_effect = OSError("device unavailable")
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_priority=priority,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        with self.assertRaisesRegex(OSError, "device unavailable"):
            await executor.async_execute_device_action("device_1", "turn_on")

        self.assertNotIn("light.living_room", priority._manual_records)  # noqa: SLF001
        self.hass.services.async_call.assert_awaited_once()

    async def test_failed_pre_dispatch_safety_gate_blocks_service_call(self) -> None:
        async def reject_dispatch() -> None:
            raise OSError("safety deadline was not persisted")

        with self.assertRaisesRegex(OSError, "deadline was not persisted"):
            await self.executor.async_execute_device_action(
                "fan_1",
                "turn_on",
                dangerous_authorized=True,
                before_dispatch=reject_dispatch,
            )

        self.hass.services.async_call.assert_not_awaited()

    async def test_descriptor_remap_during_safety_gate_blocks_service_call(self) -> None:
        expected = self.catalog.device("fan_1")
        assert expected is not None

        async def remap_target() -> None:
            self.catalog._devices["fan_1"] = ScenarioDeviceEntry(
                target_id="fan_1",
                name="Remapped fan",
                entity_id="switch.unsafe_remap",
                actions=expected.actions,
            )

        receipt = await self.executor.async_execute_device_action(
            "fan_1",
            "turn_on",
            dangerous_authorized=True,
            before_dispatch=remap_target,
            expected_entity_id=expected.entity_id,
            expected_domain="switch",
            expected_service="turn_on",
        )

        self.assertFalse(receipt["accepted"])
        self.assertEqual("dispatch_descriptor_changed", receipt["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_saved_scenario_cannot_bypass_contextual_dangerous_gate(self) -> None:
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            contextual_dangerous_resolver=lambda target_id, action_id: (
                target_id == "device_1" and action_id == "turn_on"
            ),
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="unsafe_intercom",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            )
        )

        result = await executor.async_execute(
            definition, "run-dangerous-scenario", scenario_id="unsafe"
        )

        self.assertEqual("failed", result["status"])
        self.assertEqual(
            "dangerous_action_requires_coordinator",
            result["receipts"][0]["error"],
        )
        self.hass.services.async_call.assert_not_awaited()

    async def test_sensor_run_preserves_preexisting_manual_light(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness_percent",
                    value=40,
                ),
                ScenarioAction(
                    id="wait",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=60,
                ),
                ScenarioAction(
                    id="light_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        with patch(
            "custom_components.hausman_hub.application.scenario_executor.asyncio.sleep"
        ) as sleep:
            result = await self.executor.async_execute(
                definition,
                "run-manual-priority",
                scenario_id="hall_light",
                scenario_title="Свет по движению",
                trigger_context={
                    "source": "device_state",
                    "trigger_id": "t1",
                    "target_id": "sensor_1",
                    "old_value": "off",
                    "new_value": "on",
                    "recovery": False,
                },
            )

        self.assertEqual("completed", result["status"])
        self.assertTrue(result["manual_light_priority"]["applied"])
        self.assertEqual(
            "manual_light_already_on",
            result["manual_light_priority"]["reason"],
        )
        self.assertEqual(
            ["light_on", "light_off"],
            [receipt["action_id"] for receipt in result["receipts"]],
        )
        self.assertTrue(all(receipt["skipped"] for receipt in result["receipts"]))
        journal = scenario_operation_receipt(result)
        self.assertEqual(
            ["manual_light_already_on", "manual_light_already_on"],
            [action["reason"] for action in journal["scenario"]["actions"]],
        )
        self.hass.services.async_call.assert_not_awaited()
        sleep.assert_not_awaited()

    async def test_tambur_manual_chandelier_does_not_block_presence_spots(
        self,
    ) -> None:
        self.catalog._devices["device_2"] = ScenarioDeviceEntry(
            target_id="device_2",
            name="Точки тамбура",
            entity_id="light.tambur_spots",
            actions=(
                ScenarioDeviceAction(
                    action_id="turn_on",
                    title="On",
                    domain="light",
                    service="turn_on",
                    allowed_fields=frozenset(),
                ),
            ),
        )
        self.hass.state_values["light.tambur_spots"] = SimpleNamespace(
            state="off", attributes={}
        )

        async def apply_service(
            _domain: str,
            _service: str,
            data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            self.assertTrue(blocking)
            if data.get("entity_id") == "light.tambur_spots":
                self.hass.state_values["light.tambur_spots"].state = "on"

        self.hass.services.async_call.side_effect = apply_service
        definition = _definition(
            (
                ScenarioAction(
                    id="chandelier_brightness",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness_percent",
                    value=100,
                ),
                ScenarioAction(
                    id="points_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_2",
                    action_id="turn_on",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "run-tambur-partial-manual-priority",
            scenario_id="system-tambur-adaptive-controller",
            scenario_title="Тамбур: адаптивное освещение",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
            },
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual(
            ["manual_light_already_on", None],
            [receipt.get("reason") for receipt in result["receipts"]],
        )
        self.assertTrue(result["receipts"][0]["skipped"])
        self.assertTrue(result["receipts"][1]["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.tambur_spots"},
            blocking=True,
        )

    async def test_represence_cancels_every_profile_obligation_but_dry_run_does_not(
        self,
    ) -> None:
        self.catalog._devices["device_2"] = ScenarioDeviceEntry(
            target_id="device_2",
            name="Second light",
            entity_id="light.second",
            actions=(
                ScenarioDeviceAction(
                    action_id="turn_on",
                    title="On",
                    domain="light",
                    service="turn_on",
                    allowed_fields=frozenset(),
                ),
                ScenarioDeviceAction(
                    action_id="turn_off",
                    title="Off",
                    domain="light",
                    service="turn_off",
                    allowed_fields=frozenset(),
                ),
            ),
        )
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off", attributes={}
        )
        self.hass.state_values["light.second"] = SimpleNamespace(
            state="off", attributes={}
        )
        obligations = SimpleNamespace(
            async_cancel=AsyncMock(),
            async_cancel_scenario=AsyncMock(),
            async_complete=AsyncMock(),
            async_arm=AsyncMock(),
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="old_source_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
                ScenarioAction(
                    id="new_source_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_2",
                    action_id="turn_on",
                ),
            )
        )
        trigger_context = {
            "source": "device_state",
            "trigger_id": "t1",
            "target_id": "sensor_1",
            "old_value": "off",
            "new_value": "on",
            "recovery": False,
        }

        await executor.async_execute(
            definition,
            "run-dry-represence",
            scenario_id="shower_light",
            dry_run=True,
            trigger_context=trigger_context,
        )
        obligations.async_cancel.assert_not_awaited()
        obligations.async_cancel_scenario.assert_not_awaited()

        await executor.async_execute(
            definition,
            "run-live-represence",
            scenario_id="shower_light",
            trigger_context=trigger_context,
        )
        self.assertEqual(
            [call("device_1"), call("device_2")],
            obligations.async_cancel.await_args_list,
        )
        obligations.async_cancel_scenario.assert_awaited_once_with("shower_light")

        obligations.async_cancel.reset_mock()
        self.hass.state_values["light.second"] = SimpleNamespace(
            state="on", attributes={}
        )
        await executor.async_execute_device_action("device_2", "turn_on")
        obligations.async_cancel.assert_awaited_once_with("device_2")

    async def test_positive_presence_cancels_deadline_without_light_actions(
        self,
    ) -> None:
        obligations = AsyncMock()
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="fan_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="fan_1",
                    action_id="turn_on",
                ),
            )
        )

        await executor.async_execute(
            definition,
            "run-presence-no-light",
            scenario_id="shower_comfort",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
            },
        )

        obligations.async_cancel_scenario.assert_awaited_once_with(
            "shower_comfort"
        )

    async def test_new_fan_on_plan_cancels_older_delayed_fan_off(self) -> None:
        obligations = AsyncMock()
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="fan_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="fan_1",
                    action_id="turn_on",
                ),
            )
        )

        await executor.async_execute(
            definition,
            "run-humidity-fan-on",
            scenario_id="system-shower-comfort-controller",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": 54,
                "new_value": 60,
            },
        )

        obligations.async_cancel.assert_awaited_once_with("fan_1")
        obligations.async_cancel_scenario.assert_not_awaited()

    async def test_direct_manual_fan_on_cancels_older_delayed_fan_off(self) -> None:
        obligations = AsyncMock()
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        receipt = await executor.async_execute_device_action("fan_1", "turn_on")

        self.assertTrue(receipt["accepted"])
        obligations.async_cancel.assert_awaited_once_with("fan_1")

    async def test_state_on_recovery_rejects_wrong_scenario_and_light_target(
        self,
    ) -> None:
        obligations = AsyncMock()
        obligations.async_is_current.return_value = True
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        base = {
            "scenarioId": "system-shower-comfort-controller",
            "runId": "run.recovery",
            "deadlineMs": 1,
            "ownershipRevision": None,
            "createdAt": 1,
            "generationId": "generation",
            "attempt": 1,
            "kind": "state_on",
        }

        wrong_scenario = await executor.async_reconcile_light_obligation(
            {
                **base,
                "targetId": "fan_1",
                "entityId": "switch.shower_fan",
                "scenarioId": "forged-controller",
            }
        )
        light_target = await executor.async_reconcile_light_obligation(
            {
                **base,
                "targetId": "device_1",
                "entityId": "light.living_room",
            }
        )

        self.assertEqual("invalidated", wrong_scenario)
        self.assertEqual("invalidated", light_target)
        self.hass.services.async_call.assert_not_awaited()

    async def test_confirmed_automatic_light_can_be_refreshed_until_user_changes_it(
        self,
    ) -> None:
        first_revision = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
        user_revision = datetime.now(timezone.utc)
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off", attributes={}, last_changed=first_revision - timedelta(minutes=1)
        )

        async def apply_light_state(*_args: object, **_kwargs: object) -> None:
            self.hass.state_values["light.living_room"] = SimpleNamespace(
                state="on", attributes={}, last_changed=first_revision
            )

        self.hass.services.async_call.side_effect = apply_light_state
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            )
        )
        trigger_context = {
            "source": "device_state",
            "trigger_id": "t1",
            "target_id": "sensor_1",
            "old_value": "off",
            "new_value": "on",
            "recovery": False,
        }

        first = await self.executor.async_execute(
            definition,
            "run-owned-first",
            scenario_id="hall_light",
            scenario_title="Свет по движению",
            trigger_context=trigger_context,
        )
        second = await self.executor.async_execute(
            definition,
            "run-owned-second",
            scenario_id="hall_light",
            scenario_title="Свет по движению",
            trigger_context=trigger_context,
        )

        self.assertFalse(first["manual_light_priority"]["applied"])
        self.assertFalse(second["manual_light_priority"]["applied"])
        self.assertEqual(2, self.hass.services.async_call.await_count)

        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="on", attributes={}, last_changed=user_revision
        )
        third = await self.executor.async_execute(
            definition,
            "run-owned-after-user",
            scenario_id="hall_light",
            scenario_title="Свет по движению",
            trigger_context=trigger_context,
        )

        self.assertTrue(third["manual_light_priority"]["applied"])
        self.assertEqual(2, self.hass.services.async_call.await_count)

    async def test_light_state_trigger_is_not_mistaken_for_sensor(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="adjust_light",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness_percent",
                    value=40,
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "run-light-trigger",
            scenario_id="office_adaptive_light",
            scenario_title="Кабинет: адаптивный свет",
            trigger_context={
                "source": "device_state",
                "trigger_id": "t1",
                "target_id": "device_1",
                "old_value": "off",
                "new_value": "on",
                "recovery": False,
            },
        )

        self.assertFalse(result["manual_light_priority"]["applied"])
        self.hass.services.async_call.assert_awaited_once()

    async def test_sensor_run_preserves_light_but_still_starts_fan(self) -> None:
        shower_fan_target = "entity_afef5df0e0cae309"
        self.catalog._devices[shower_fan_target] = ScenarioDeviceEntry(
            target_id=shower_fan_target,
            name="Выключатель душевая доп свет 2",
            entity_id="switch.shower_fan",
            actions=self.catalog._devices["fan_1"].actions,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="fan_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id=shower_fan_target,
                    target_name="Душевая: вытяжка",
                    action_id="turn_on",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "run-mixed-priority",
            scenario_id="shower_light_and_fan",
            scenario_title="Душевая: свет и вытяжка",
            trigger_context={
                "source": "device_state",
                "trigger_id": "t1",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
                "recovery": False,
            },
        )

        self.assertEqual("completed", result["status"])
        self.assertTrue(result["manual_light_priority"]["applied"])
        self.assertTrue(result["receipts"][0]["skipped"])
        self.assertNotIn("skipped", result["receipts"][1])
        self.hass.services.async_call.assert_awaited_once_with(
            "switch", "turn_on", {"entity_id": "switch.shower_fan"}, blocking=True
        )

    async def test_sensor_toggle_is_terminal_skip_without_service_call(self) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off", attributes={}
        )
        self.catalog._devices["toggle_1"] = ScenarioDeviceEntry(
            target_id="toggle_1",
            name="Световая кнопка",
            entity_id="light.living_room",
            actions=(
                ScenarioDeviceAction(
                    action_id="toggle",
                    title="Переключить",
                    domain="light",
                    service="toggle",
                    allowed_fields=frozenset(),
                ),
            ),
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="toggle",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="toggle_1",
                    action_id="toggle",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "run-sensor-toggle",
            scenario_id="sensor_toggle",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
            },
        )

        self.assertEqual("completed", result["status"])
        self.assertTrue(result["receipts"][0]["skipped"])
        self.assertEqual(
            "automatic_toggle_forbidden", result["receipts"][0]["reason"]
        )
        self.hass.services.async_call.assert_not_awaited()

    async def test_direct_device_action_dry_run_sends_no_service_call(self) -> None:
        receipt = await self.executor.async_execute_device_action(
            "device_1", "turn_on", correlation_id="corr-dry-run", dry_run=True
        )
        self.assertTrue(receipt["accepted"])
        self.assertFalse(receipt["confirmed"])
        self.assertTrue(receipt["dryRun"])
        self.assertEqual("dry_run", receipt["reason"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_intercom_release_requires_off_read_back(self) -> None:
        self.executor._read_back_device = AsyncMock(
            return_value={"matched": True}
        )
        confirmed = await self.executor.async_release_intercom_switch(
            "switch.entry_intercom"
        )
        self.assertTrue(confirmed)
        self.hass.services.async_call.assert_awaited_once_with(
            "switch",
            "turn_off",
            {"entity_id": "switch.entry_intercom"},
            blocking=True,
        )
        self.executor._read_back_device.assert_awaited_once_with(
            "switch.entry_intercom",
            "turn_off",
            None,
            after_revision=None,
            require_new_evidence=True,
        )

    async def test_intercom_release_rejects_unchanged_old_off_state(self) -> None:
        observed = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.hass.state_values["switch.entry_intercom"] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=observed,
        )

        confirmed = await self.executor.async_release_intercom_switch(
            "switch.entry_intercom"
        )

        self.assertFalse(confirmed)
        self.hass.services.async_call.assert_awaited_once()

    async def test_intercom_toggle_dispatches_turn_on_and_requires_new_state(self) -> None:
        entity_id = "switch.entry_intercom"
        self.catalog._devices["intercom_1"] = ScenarioDeviceEntry(
            target_id="intercom_1",
            name="Intercom",
            entity_id=entity_id,
            actions=(
                ScenarioDeviceAction(
                    action_id="toggle",
                    title="Open",
                    domain="switch",
                    service="toggle",
                    allowed_fields=frozenset(),
                ),
            ),
        )
        self.hass.state_values[entity_id] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            self.assertEqual("turn_on", service)
            self.hass.state_values[entity_id] = SimpleNamespace(
                state="on",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            contextual_dangerous_resolver=lambda target_id, action_id: (
                target_id == "intercom_1" and action_id == "toggle"
            ),
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        receipt = await executor.async_execute_device_action(
            "intercom_1",
            "toggle",
            dangerous_authorized=True,
            before_dispatch=AsyncMock(),
            expected_entity_id=entity_id,
            expected_domain="switch",
            expected_service="toggle",
        )

        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["confirmed"])
        self.assertEqual("on", receipt["readBack"]["observedState"])
        self.hass.services.async_call.assert_awaited_once_with(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True
        )

    async def test_failed_media_vendor_does_not_block_core_device_actions(self) -> None:
        breaker = VendorCircuitBreaker(
            timeout_seconds=1, failure_threshold=1, cooldown_seconds=60
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            vendor_resilience=breaker,
        )
        self.hass.services.async_call.side_effect = RuntimeError("vendor offline")
        failed = await executor.async_execute_device_action("media_1", "turn_on")
        self.assertFalse(failed["accepted"])
        self.assertEqual("vendor_error", failed["error"])
        failed_fast = await executor.async_execute_device_action("media_1", "turn_on")
        self.assertEqual("vendor_circuit_open", failed_fast["error"])
        self.assertEqual(1, self.hass.services.async_call.await_count)

        self.hass.services.async_call.side_effect = None
        core = await executor.async_execute_device_action("device_1", "turn_on")
        self.assertTrue(core["accepted"])
        self.assertTrue(core["confirmed"])

    async def test_closed_cover_is_not_commanded_again(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="cover_1",
                    action_id="close_cover",
                ),
            ),
            idempotent_actions=True,
        )

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

    async def test_idempotent_climate_target_is_not_commanded_again(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="climate_1",
                    action_id="set_temperature",
                    value=22,
                ),
            ),
            idempotent_actions=True,
        )

        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="climate-target"
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual("already_in_target_state", result["receipts"][0]["reason"])
        self.assertTrue(result["receipts"][0]["skipped"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_repeated_correlation_keeps_physical_action_idempotent(self) -> None:
        light = SimpleNamespace(state="off", attributes={})
        self.hass.states = SimpleNamespace(
            get=lambda entity_id: light if entity_id == "light.living_room" else None
        )

        async def apply_service(*_args: object, **_kwargs: object) -> None:
            light.state = "on"

        self.hass.services.async_call.side_effect = apply_service
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            ),
            idempotent_actions=True,
        )

        first = await self.executor.async_execute(
            definition, "corr.repeat-1", scenario_id="repeat_scenario"
        )
        repeated = await self.executor.async_execute(
            definition, "corr.repeat-1", scenario_id="repeat_scenario"
        )

        self.assertEqual("completed", first["status"])
        self.assertEqual("completed", repeated["status"])
        self.assertEqual("already_in_target_state", repeated["receipts"][0]["reason"])
        self.assertEqual(1, self.hass.services.async_call.await_count)

    async def test_stale_unknown_on_is_reasserted_with_new_evidence(self) -> None:
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="on", attributes={}, last_updated=stale
        )

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            self.hass.state_values["light.living_room"] = SimpleNamespace(
                state="on" if service == "turn_on" else "off",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            )

        self.hass.services.async_call.side_effect = apply_service
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            ),
            idempotent_actions=True,
        )

        result = await self.executor.async_execute(
            definition,
            "stale-false-on.1",
            scenario_id="stale_false_on",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
            },
        )

        self.assertEqual("completed", result["status"])
        self.assertFalse(result["receipts"][0].get("skipped", False))
        self.assertTrue(result["receipts"][0]["read_back"]["isNewEvidence"])
        self.assertTrue(
            self.executor._light_priority.is_owned(
                "light.living_room", self.hass
            )
        )

        off_result = await self.executor.async_execute(
            _definition(
                (
                    ScenarioAction(
                        id="light_off",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id="device_1",
                        action_id="turn_off",
                    ),
                )
            ),
            "stale-false-on.off",
            scenario_id="absence_light_off",
            trigger_context={"source": "device_state", "target_id": "sensor_1"},
        )

        self.assertEqual("completed", off_result["status"])
        self.assertTrue(off_result["receipts"][0]["confirmed"])
        self.assertEqual(2, self.hass.services.async_call.await_count)

    async def test_stale_on_reassert_is_attempted_once_per_evidence_revision(
        self,
    ) -> None:
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        unchanged = SimpleNamespace(
            state="on", attributes={}, last_updated=stale
        )
        self.hass.state_values["light.living_room"] = unchanged
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            ),
            idempotent_actions=True,
        )
        trigger = {
            "source": "device_state",
            "target_id": "sensor_1",
            "old_value": "off",
            "new_value": "on",
        }

        first = await self.executor.async_execute(
            definition,
            "stale-budget.1",
            scenario_id="stale_budget",
            trigger_context=trigger,
        )
        second = await self.executor.async_execute(
            definition,
            "stale-budget.2",
            scenario_id="stale_budget",
            trigger_context=trigger,
        )

        self.assertFalse(first["confirmed"])
        self.assertEqual("failed", second["status"])
        self.assertEqual(
            "reassert_budget_exhausted", second["receipts"][0]["error"]
        )
        self.assertEqual(1, self.hass.services.async_call.await_count)

    async def test_api_and_scenario_share_one_stale_reassert_budget(self) -> None:
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        state = SimpleNamespace(state="on", attributes={}, last_changed=stale)
        self.hass.state_values["light.living_room"] = state
        evidence = evidence_snapshot(
            target_id="device_1",
            state=state,
            allowed_actions=("turn_on", "turn_off"),
        )

        api_receipt = await self.executor.async_execute_device_action(
            "device_1",
            "turn_on",
            automatic_reassert=True,
            reassert_claim_id="api.reassert.shared",
            force_new_readback=True,
            expected_evidence_revision=str(evidence["evidenceRevision"]),
            expected_evidence_sequence=int(evidence["evidenceSequence"]),
        )
        scenario_result = await self.executor.async_execute(
            _definition(
                (
                    ScenarioAction(
                        id="light_on",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id="device_1",
                        action_id="turn_on",
                    ),
                ),
                idempotent_actions=True,
            ),
            "scenario.reassert.shared",
            scenario_id="stale_budget_shared",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
            },
        )

        self.assertFalse(api_receipt["confirmed"])
        self.assertEqual("failed", scenario_result["status"])
        self.assertEqual(
            "reassert_budget_exhausted",
            scenario_result["receipts"][0]["error"],
        )
        self.assertEqual(1, self.hass.services.async_call.await_count)

    async def test_fresh_physical_on_after_plan_blocks_profile_dispatch(self) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )

        async def apply_service(
            domain: str, _service: str, *_args: object, **_kwargs: object
        ) -> None:
            if domain == "notify":
                self.hass.state_values["light.living_room"] = SimpleNamespace(
                    state="on",
                    attributes={},
                    last_updated=datetime.now(timezone.utc) + timedelta(seconds=1),
                )

        self.hass.services.async_call.side_effect = apply_service
        definition = _definition(
            (
                ScenarioAction(
                    id="notice",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Проверка перед светом",
                ),
                ScenarioAction(
                    id="brightness",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness_percent",
                    value=40,
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "late-physical-on.1",
            scenario_id="presence_light",
            trigger_context={"source": "device_state", "target_id": "sensor_1"},
        )

        self.assertEqual("manual_light_already_on", result["receipts"][1]["reason"])
        self.assertEqual(1, self.hass.services.async_call.await_count)
        self.assertIn(
            "light.living_room",
            self.executor._light_priority._manual_records,
        )

    async def test_reassert_changed_evidence_blocks_dispatch_under_authority_lock(
        self,
    ) -> None:
        stale_state = SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        self.hass.state_values["light.living_room"] = stale_state
        evidence = evidence_snapshot(
            target_id="device_1",
            state=stale_state,
            allowed_actions=("turn_on", "turn_off"),
        )
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )

        result = await self.executor.async_execute_device_action(
            "device_1",
            "turn_on",
            automatic_reassert=True,
            force_new_readback=True,
            expected_evidence_revision=str(evidence["evidenceRevision"]),
            expected_evidence_sequence=int(evidence["evidenceSequence"]),
        )

        self.assertFalse(result["accepted"])
        self.assertEqual("stale_reassert_evidence", result["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_delayed_light_off_clears_obligation_after_deferred_readback(
        self,
    ) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        obligations = AsyncMock()

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            self.hass.state_values["light.living_room"] = SimpleNamespace(
                state="on" if service == "turn_on" else "off",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="wait_five_minutes",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=300,
                ),
                ScenarioAction(
                    id="light_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        with patch(
            "custom_components.hausman_hub.application.scenario_executor.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await executor.async_execute(
                definition,
                "shower-owned-off.1",
                scenario_id="shower_absence",
                trigger_context={"source": "device_state", "target_id": "sensor_1"},
            )

        self.assertTrue(result["receipts"][2]["confirmed"])
        obligations.async_arm.assert_awaited_once()
        obligations.async_complete.assert_awaited_with("device_1")
        obligations.async_retry.assert_not_awaited()

    async def test_shower_delayed_fan_off_arms_state_revision_obligation(
        self,
    ) -> None:
        fan_revision = datetime.now(timezone.utc)
        self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
            state="on", attributes={}, last_changed=fan_revision
        )
        obligations = AsyncMock()

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            if service == "turn_off":
                self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
                    state="off",
                    attributes={},
                    last_changed=datetime.now(timezone.utc),
                )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="absence_wait",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=300,
                ),
                ScenarioAction(
                    id="fan_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="fan_1",
                    action_id="turn_off",
                ),
            )
        )

        with patch(
            "custom_components.hausman_hub.application.scenario_executor.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await executor.async_execute(
                definition,
                "shower-fan-off.1",
                scenario_id="system-shower-comfort-controller",
                trigger_context={"source": "device_state", "target_id": "sensor_1"},
            )

        self.assertEqual("completed", result["status"])
        obligations.async_arm.assert_awaited_once()
        arm = obligations.async_arm.await_args.kwargs
        self.assertEqual("fan_1", arm["target_id"])
        self.assertEqual("switch.shower_fan", arm["entity_id"])
        self.assertEqual("state_on", arm["kind"])
        self.assertIsNone(arm["ownership_revision"])
        obligations.async_complete.assert_awaited_with("fan_1")

    async def test_managed_shower_multi_target_guard_evidence_survives_restart(
        self,
    ) -> None:
        """The executor, not a direct store call, must keep guard evidence immutable."""

        class Store:
            def __init__(self) -> None:
                self.payload: dict[str, object] | None = None

            async def async_load(self) -> object | None:
                return copy.deepcopy(self.payload)

            async def async_save(self, payload: dict[str, object]) -> None:
                self.payload = copy.deepcopy(payload)

        now = datetime.now(timezone.utc)

        def state(value: str, *, attributes: dict[str, object] | None = None, age: int = 0) -> SimpleNamespace:
            observed = now - timedelta(minutes=age)
            return SimpleNamespace(
                state=value,
                attributes=attributes or {},
                last_changed=observed,
                last_updated=observed,
            )

        targets = {
            "main": "switch.shower_main",
            "extra": "switch.shower_extra",
            "cabinet": "switch.shower_cabinet",
            "fan": "switch.shower_fan",
        }
        for target_id, entity_id in targets.items():
            self.catalog._devices[target_id] = ScenarioDeviceEntry(
                target_id=target_id,
                name=f"Реле {target_id}",
                entity_id=entity_id,
                actions=(
                    ScenarioDeviceAction(
                        action_id="turn_off",
                        title="Off",
                        domain="switch",
                        service="turn_off",
                        allowed_fields=frozenset(),
                    ),
                ),
            )
            self.hass.state_values[entity_id] = state("on")
        self.catalog._devices["shower_presence"] = ScenarioDeviceEntry(
            target_id="shower_presence",
            name="Присутствие в душевой",
            entity_id="binary_sensor.shower_presence",
            actions=(),
        )
        self.hass.state_values["binary_sensor.shower_presence"] = state("off")

        definition = ScenarioDefinition(
            version=1,
            execution_mode=ScenarioExecutionMode.RESTART,
            execution_backend=ScenarioExecutionBackend.NODE_RED,
            command_mode=ScenarioCommandMode.LIVE,
            triggers=(
                ScenarioTrigger(
                    id="presence",
                    type=ScenarioTriggerType.DEVICE_STATE,
                    target_id="shower_presence",
                    property="state",
                    comparison=ScenarioComparison.EQUALS,
                    value="off",
                ),
            ),
            conditions=(),
            actions=(
                ScenarioAction(
                    id="absence_wait",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=300,
                ),
                *(ScenarioAction(
                    id=f"set_{target_id}_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id=target_id,
                    action_id="turn_off",
                ) for target_id in targets),
            ),
            node_red=ScenarioNodeRedMetadata(
                input_target_ids=("shower_presence",),
            ),
        )
        store = Store()
        obligations = LightSafetyObligations(store, now_ms=lambda: 1_000)
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        generations = await executor._async_arm_future_light_offs(
            definition.actions,
            0,
            scenario_id="system-shower-comfort-controller",
            run_id="shower.absence.multi",
            definition=definition,
        )

        self.assertEqual(set(targets), set(generations))
        self.assertEqual(4, len(set(generations.values())))
        assert store.payload is not None
        records = store.payload["records"]
        self.assertEqual(4, len(records))
        self.assertTrue(
            all(
                record["guardEntityIds"] == ["binary_sensor.shower_presence"]
                and record["guardEvidence"]
                == {"binary_sensor.shower_presence": now.isoformat()}
                for record in records
            )
        )
        persisted = copy.deepcopy(store.payload)

        async def apply_turn_off(
            _domain: str, service: str, data: dict[str, str], **_kwargs: object
        ) -> None:
            self.assertEqual("turn_off", service)
            entity_id = data["entity_id"]
            self.hass.state_values[entity_id] = state("off")

        self.hass.services.async_call.side_effect = apply_turn_off
        restarted = LightSafetyObligations(store, now_ms=lambda: 1_000)
        await restarted.async_load()
        recovery = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=restarted,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )
        outcomes = [
            await recovery.async_reconcile_light_obligation(record)
            for record in records
        ]
        self.assertEqual(["confirmed"] * 4, outcomes)
        self.assertEqual(4, self.hass.services.async_call.await_count)
        self.assertEqual(
            set(targets.values()),
            {call.args[2]["entity_id"] for call in self.hass.services.async_call.await_args_list},
        )

        for label, presence in (
            ("stale", state("off", age=30)),
            ("on", state("on")),
            ("restored", state("off", attributes={"restored": True})),
            ("cached", state("off", attributes={"cached": True})),
            ("assumed", state("off", attributes={"assumed_state": True})),
        ):
            with self.subTest(label=label):
                self.hass.services.async_call.reset_mock()
                self.hass.state_values["binary_sensor.shower_presence"] = presence
                for entity_id in targets.values():
                    self.hass.state_values[entity_id] = state("on")
                unsafe_store = Store()
                unsafe_store.payload = copy.deepcopy(persisted)
                unsafe = LightSafetyObligations(unsafe_store, now_ms=lambda: 1_000)
                await unsafe.async_load()
                unsafe_recovery = ScenarioExecutor(
                    self.hass,
                    self.catalog,
                    self.executor._run_callback,
                    light_safety_obligations=unsafe,
                    readback_window_seconds=0.02,
                    readback_interval_seconds=0.01,
                )
                unsafe_outcomes = [
                    await unsafe_recovery.async_reconcile_light_obligation(record)
                    for record in unsafe_store.payload["records"]
                ]
                self.assertEqual([RECONCILE_INVALIDATED] * 4, unsafe_outcomes)
                self.hass.services.async_call.assert_not_awaited()

    async def test_restarted_shower_fan_obligation_without_guard_proof_is_invalidated(
        self,
    ) -> None:
        fan_revision = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
            state="on", attributes={}, last_changed=fan_revision
        )
        obligations = AsyncMock()
        obligations.async_is_current.return_value = True

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            if service == "turn_off":
                self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
                    state="off",
                    attributes={},
                    last_changed=datetime.now(timezone.utc),
                )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        outcome = await executor.async_reconcile_light_obligation(
            {
                "targetId": "fan_1",
                "entityId": "switch.shower_fan",
                "scenarioId": "system-shower-comfort-controller",
                "runId": "shower-fan-off.restart",
                "deadlineMs": 1,
                "ownershipRevision": None,
                "createdAt": 1,
                "generationId": "fan-generation",
                "attempt": 1,
                "kind": "state_on",
                "guardEntityIds": ["binary_sensor.shower_presence"],
            }
        )

        self.assertEqual("invalidated", outcome)
        self.hass.services.async_call.assert_not_awaited()

    async def test_restarted_shower_fan_obligation_without_guard_proof_rejects_new_revision(
        self,
    ) -> None:
        old_revision = datetime.now(timezone.utc) - timedelta(minutes=6)
        self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
            state="on",
            attributes={},
            last_changed=old_revision + timedelta(minutes=1),
        )
        obligations = AsyncMock()
        obligations.async_is_current.return_value = True
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            if service == "turn_off":
                self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
                    state="off",
                    attributes={},
                    last_changed=datetime.now(timezone.utc),
                )

        self.hass.services.async_call.side_effect = apply_service

        outcome = await executor.async_reconcile_light_obligation(
            {
                "targetId": "fan_1",
                "entityId": "switch.shower_fan",
                "scenarioId": "system-shower-comfort-controller",
                "runId": "shower-fan-off.changed",
                "deadlineMs": 1,
                "ownershipRevision": None,
                "createdAt": 1,
                "generationId": "fan-generation",
                "attempt": 1,
                "kind": "state_on",
                "guardEntityIds": ["binary_sensor.shower_presence"],
            }
        )

        self.assertEqual("invalidated", outcome)
        self.hass.services.async_call.assert_not_awaited()

    async def test_restarted_shower_fan_obligation_without_guard_proof_is_invalidated_when_off(
        self,
    ) -> None:
        self.hass.state_values["switch.shower_fan"] = SimpleNamespace(
            state="off", attributes={}, last_changed=datetime.now(timezone.utc)
        )
        obligations = AsyncMock()
        obligations.async_is_current.return_value = True
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        outcome = await executor.async_reconcile_light_obligation(
            {
                "targetId": "fan_1",
                "entityId": "switch.shower_fan",
                "scenarioId": "system-shower-comfort-controller",
                "runId": "shower-fan-off.already-off",
                "deadlineMs": 1,
                "ownershipRevision": None,
                "createdAt": 1,
                "generationId": "fan-generation",
                "attempt": 1,
                "kind": "state_on",
                "guardEntityIds": ["binary_sensor.shower_presence"],
            }
        )

        self.assertEqual("invalidated", outcome)
        self.hass.services.async_call.assert_not_awaited()

    async def test_failed_delayed_light_off_starts_durable_retry(self) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        obligations = AsyncMock()

        async def apply_service(
            _domain: str, service: str, *_args: object, **_kwargs: object
        ) -> None:
            if service == "turn_on":
                self.hass.state_values["light.living_room"] = SimpleNamespace(
                    state="on",
                    attributes={},
                    last_updated=datetime.now(timezone.utc),
                )
            else:
                raise OSError("injected delayed turn-off failure")

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_safety_obligations=obligations,
            readback_window_seconds=0.01,
            readback_interval_seconds=0.01,
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="light_on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="wait_five_minutes",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=300,
                ),
                ScenarioAction(
                    id="light_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        async def fast_sleep(_delay: float) -> None:
            await asyncio.get_running_loop().run_in_executor(None, lambda: None)

        with patch(
            "custom_components.hausman_hub.application.scenario_executor.asyncio.sleep",
            side_effect=fast_sleep,
        ):
            result = await executor.async_execute(
                definition,
                "shower-owned-off.failed",
                scenario_id="shower_absence",
                trigger_context={"source": "device_state", "target_id": "sensor_1"},
            )

        self.assertEqual("failed", result["receipts"][2]["status"])
        self.assertFalse(result["receipts"][2].get("confirmed", False))
        obligations.async_retry.assert_awaited_once_with(
            "device_1", physical_attempted=True
        )

    async def test_automatic_turn_off_skips_light_without_confirmed_ownership(self) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="light_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "automatic-off-no-owner.1",
            scenario_id="absence_light_off",
            trigger_context={"source": "device_state", "target_id": "sensor_1"},
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual(
            "automatic_ownership_missing", result["receipts"][0]["reason"]
        )
        self.assertTrue(result["receipts"][0]["skipped"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_departure_shutdown_plans_light_off_without_automation_ownership(
        self,
    ) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="on",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="light_off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "departure-off.1",
            scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state", "target_id": "motion_1"},
            dry_run=True,
        )

        receipt = result["receipts"][0]
        self.assertEqual("completed", receipt["status"])
        self.assertEqual("shadow_plan", receipt["reason"])
        self.assertNotIn("skipped", receipt)
        self.hass.services.async_call.assert_not_awaited()

    async def test_departure_shutdown_authority_is_preserved_for_nested_scenario(
        self,
    ) -> None:
        nested_contexts: list[object] = []

        async def capture_callback(
            scenario_id: str,
            **kwargs: object,
        ) -> dict[str, Any]:
            nested_contexts.append(kwargs.get("trigger_context"))
            return {
                "run_id": f"nested-{scenario_id}",
                "scenario_id": scenario_id,
                "status": "completed",
                "receipts": [],
            }

        executor = ScenarioExecutor(self.hass, self.catalog, capture_callback)
        definition = _definition(
            (
                ScenarioAction(
                    id="run_away",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="scenario_manual_away",
                ),
            )
        )

        result = await executor.async_execute(
            definition,
            "departure-nested.1",
            scenario_id="system-away-turn-off",
            trigger_context={"source": "device_state", "target_id": "away_1"},
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, len(nested_contexts))
        self.assertIsInstance(nested_contexts[0], dict)
        assert isinstance(nested_contexts[0], dict)
        self.assertIs(True, nested_contexts[0]["departure_shutdown_authorized"])

    async def test_managed_fade_skips_light_without_confirmed_ownership(self) -> None:
        self.hass.state_values["light.living_room"] = SimpleNamespace(
            state="on",
            attributes={"brightness": 255},
            last_updated=datetime.now(timezone.utc),
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="fade_50",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness_percent",
                    value=50,
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "managed-fade-no-owner.1",
            scenario_id="system-tambur-adaptive-controller",
            trigger_context={"source": "device_state", "target_id": "sensor_1"},
        )

        self.assertEqual(
            "manual_light_already_on", result["receipts"][0]["reason"]
        )
        self.assertTrue(result["receipts"][0]["skipped"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_unavailable_condition_fails_closed_before_action(self) -> None:
        self.catalog._devices["sensor_1"] = ScenarioDeviceEntry(
            target_id="sensor_1",
            name="Leak sensor",
            entity_id="binary_sensor.leak",
            actions=(),
        )
        original_get = self.hass.states.get
        self.hass.states.get = lambda entity_id: (
            SimpleNamespace(state="unavailable", attributes={})
            if entity_id == "binary_sensor.leak"
            else original_get(entity_id)
        )
        definition = ScenarioDefinition(
            version=1,
            execution_mode=ScenarioExecutionMode.SINGLE,
            triggers=(ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),),
            conditions=(
                ScenarioCondition(
                    id="leak_ok",
                    type=ScenarioConditionType.DEVICE_STATE,
                    target_id="sensor_1",
                    property="state",
                    comparison=ScenarioComparison.EQUALS,
                    value="off",
                ),
            ),
            actions=(
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            ),
        )

        result = await self.executor.async_execute(
            definition, "run-stale", scenario_id="water-safety"
        )

        self.assertEqual("failed", result["status"])
        self.assertEqual("stale_critical_evidence", result["reason"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_empty_catalog_fails_closed_without_physical_command(self) -> None:
        self.executor.replace_catalog(ScenarioCatalog(devices={}, scenarios={}))
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition, "run-empty-catalog", scenario_id="empty_catalog"
        )

        self.assertEqual("failed", result["status"])
        self.assertIn("not available", result["receipts"][0]["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_conditions_use_one_evidence_snapshot(self) -> None:
        self.catalog._devices["sensor_1"] = ScenarioDeviceEntry(
            target_id="sensor_1",
            name="Motion sensor",
            entity_id="binary_sensor.motion",
            actions=(),
        )
        calls = 0
        original_get = self.hass.states.get

        def changing_get(entity_id: str) -> object | None:
            nonlocal calls
            if entity_id == "binary_sensor.motion":
                calls += 1
                return SimpleNamespace(
                    state="on" if calls == 1 else "off",
                    attributes={},
                )
            return original_get(entity_id)

        self.hass.states.get = changing_get
        definition = ScenarioDefinition(
            version=1,
            execution_mode=ScenarioExecutionMode.SINGLE,
            triggers=(ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),),
            conditions=(
                ScenarioCondition(
                    id="motion_on",
                    type=ScenarioConditionType.DEVICE_STATE,
                    target_id="sensor_1",
                    property="state",
                    comparison=ScenarioComparison.EQUALS,
                    value="on",
                ),
            ),
            actions=(
                ScenarioAction(
                    id="notify",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Motion",
                ),
            ),
        )

        result = await self.executor.async_execute(
            definition, "run-snapshot", scenario_id="snapshot-test"
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual(1, calls)
        self.assertEqual(32, len(result["evidence_revision"]))

    async def test_failure_after_completed_action_is_partial(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="notify",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Started",
                ),
                ScenarioAction(
                    id="missing",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="missing_device",
                    action_id="turn_on",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition, "run-partial", scenario_id="partial-test"
        )

        self.assertEqual("partial", result["status"])
        self.assertEqual(
            ["completed", "failed"], [item["status"] for item in result["receipts"]]
        )

    async def test_stale_evidence_after_delay_is_partial_and_blocks_valve(self) -> None:
        definition = ScenarioDefinition(
            version=1,
            execution_mode=ScenarioExecutionMode.SINGLE,
            triggers=(ScenarioTrigger(id="t1", type=ScenarioTriggerType.MANUAL),),
            conditions=(),
            actions=(
                ScenarioAction(
                    id="wait",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=2,
                ),
                ScenarioAction(
                    id="valve_close",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="valve_1",
                    action_id="close_valve",
                ),
            ),
            safety_policy=ScenarioSafetyPolicy(max_evidence_age_seconds=1),
        )

        with (
            patch(
                "custom_components.hausman_hub.application.scenario_executor.time.time",
                side_effect=(1000.0, 1000.0, 1000.0, 1002.0, 1002.0),
            ),
            patch(
                "custom_components.hausman_hub.application."
                "scenario_executor.asyncio.sleep",
                new=AsyncMock(),
            ),
        ):
            result = await self.executor.async_execute(
                definition,
                "run-stale-1",
                scenario_id="stale_evidence",
            )

        self.assertEqual("partial", result["status"])
        self.assertEqual("stale_critical_evidence", result["receipts"][1]["error"])
        self.hass.services.async_call.assert_not_awaited()
        normalized = scenario_operation_receipt(result)
        self.assertFalse(normalized["accepted"])
        self.assertFalse(normalized["confirmed"])
        self.assertEqual("partial", normalized["scenario"]["outcome"])

    async def test_open_cover_still_receives_close_command(self) -> None:
        original_get = self.hass.states.get
        self.hass.states.get = lambda entity_id: (
            SimpleNamespace(state="open", attributes={})
            if entity_id == "cover.living_room"
            else original_get(entity_id)
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="cover_1",
                    action_id="close_cover",
                ),
            )
        )

        await self.executor.async_execute(
            definition, "run-1", scenario_id="close-curtains"
        )

        self.hass.services.async_call.assert_awaited_once_with(
            "cover",
            "close_cover",
            {"entity_id": "cover.living_room"},
            blocking=True,
        )

    async def test_unpowered_device_action_is_blocked_without_service_call(
        self,
    ) -> None:
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
            power_dependency_resolver=_power_link,
        )
        receipt = await executor.async_execute_device_action("device_1", "turn_on")
        self.assertFalse(receipt["accepted"])
        self.assertEqual("failed", receipt["status"])
        self.assertEqual("power_source_off", receipt["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_requires_on_rejects_fresh_restored_source_cache(self) -> None:
        observed = datetime.now(timezone.utc)
        self.hass.states = SimpleNamespace(
            get={
                "light.living_room": SimpleNamespace(
                    state="off", attributes={}, last_updated=observed
                ),
                "switch.wall": SimpleNamespace(
                    state="on",
                    attributes={"restored": True},
                    last_updated=observed,
                ),
            }.get
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=_power_link,
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertFalse(receipt["accepted"])
        self.assertEqual("power_source_unavailable", receipt["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_source_attribute_update_does_not_refresh_power_evidence(self) -> None:
        now = datetime.now(timezone.utc)
        self.hass.states = SimpleNamespace(
            get={
                "light.living_room": SimpleNamespace(
                    state="off", attributes={}, last_changed=now
                ),
                "switch.wall": SimpleNamespace(
                    state="on",
                    attributes={"telemetry": 42},
                    last_changed=now - timedelta(minutes=10),
                    last_updated=now,
                ),
            }.get
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=_power_link,
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertFalse(receipt["accepted"])
        self.assertEqual("power_source_unavailable", receipt["error"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_auto_dependency_powers_source_before_target_command(self) -> None:
        states = {
            "light.living_room": SimpleNamespace(state="off", attributes={}),
            "switch.wall": SimpleNamespace(state="off", attributes={}),
        }
        self.hass.states = SimpleNamespace(get=states.get)

        async def apply_service(
            domain: str,
            service: str,
            data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            self.assertTrue(blocking)
            entity_id = str(data["entity_id"])
            if service == "turn_on":
                states[entity_id] = SimpleNamespace(state="on", attributes={})

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=2
            ),
        )

        with patch(
            "custom_components.hausman_hub.application.scenario_executor.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep:
            receipt = await executor.async_execute_device_action(
                "device_1", "turn_on"
            )

        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["confirmed"])
        self.assertEqual(
            [
                call(
                    "switch",
                    "turn_on",
                    {"entity_id": "switch.wall"},
                    blocking=True,
                ),
                call(
                    "light",
                    "turn_on",
                    {"entity_id": "light.living_room"},
                    blocking=True,
                ),
            ],
            self.hass.services.async_call.await_args_list,
        )
        self.assertEqual("on", states["switch.wall"].state)
        sleep.assert_awaited_once_with(2.0)

    async def test_effective_power_off_clears_manual_claim_on_first_auto_run(
        self,
    ) -> None:
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        states = {
            "light.living_room": SimpleNamespace(
                state="on", attributes={}, last_changed=stale
            ),
            "switch.wall": SimpleNamespace(
                state="off",
                attributes={},
                last_changed=datetime.now(timezone.utc),
            ),
            "binary_sensor.hall_motion": SimpleNamespace(
                state="on", attributes={}, last_changed=datetime.now(timezone.utc)
            ),
        }
        self.hass.states = SimpleNamespace(get=states.get)
        priority = LightAutomationPriority()
        await priority.note_direct_action(
            "device_1",
            "turn_on",
            {"status": "completed", "confirmed": True},
            self.catalog,
            self.hass,
        )

        async def apply_service(
            _domain: str,
            service: str,
            data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            self.assertTrue(blocking)
            if service == "turn_on":
                states[str(data["entity_id"])] = SimpleNamespace(
                    state="on",
                    attributes={},
                    last_changed=datetime.now(timezone.utc),
                )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            light_priority=priority,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=0
            ),
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
        )

        result = await executor.async_execute(
            _definition(
                (
                    ScenarioAction(
                        id="light_on",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id="device_1",
                        action_id="turn_on",
                    ),
                )
            ),
            "run-first-presence",
            scenario_id="tambur-light",
            trigger_context={
                "source": "device_state",
                "target_id": "sensor_1",
                "old_value": "off",
                "new_value": "on",
            },
        )

        self.assertEqual("completed", result["status"])
        self.assertFalse(result["receipts"][0].get("skipped", False))
        self.assertEqual(
            ["switch.wall", "light.living_room"],
            [
                call_item.args[2]["entity_id"]
                for call_item in self.hass.services.async_call.await_args_list
            ],
        )

    async def test_stale_source_on_is_confirmed_before_dependent_light(self) -> None:
        states = {
            "light.living_room": SimpleNamespace(
                state="off",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            ),
            "switch.wall": SimpleNamespace(
                state="on",
                attributes={},
                last_updated=datetime.now(timezone.utc) - timedelta(minutes=10),
            ),
        }
        self.hass.states = SimpleNamespace(get=states.get)

        async def apply_service(
            domain: str,
            service: str,
            data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            entity_id = str(data["entity_id"])
            states[entity_id] = SimpleNamespace(
                state="on",
                attributes={},
                last_updated=datetime.now(timezone.utc),
            )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=0
            ),
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertTrue(receipt["confirmed"])
        self.assertTrue(receipt["power_precondition"]["sourceTurnedOn"])
        self.assertIsNotNone(
            receipt["power_precondition"]["sourceEvidenceRevision"]
        )
        self.assertEqual(
            ["switch.wall", "light.living_room"],
            [
                current.args[2]["entity_id"]
                for current in self.hass.services.async_call.await_args_list
            ],
        )

    async def test_restored_source_marker_must_clear_after_turn_on_readback(
        self,
    ) -> None:
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        states = {
            "light.living_room": SimpleNamespace(
                state="off", attributes={}, last_updated=datetime.now(timezone.utc)
            ),
            "switch.wall": SimpleNamespace(
                state="on", attributes={"restored": True}, last_updated=stale
            ),
        }
        self.hass.states = SimpleNamespace(get=states.get)

        async def apply_service(
            _domain: str,
            _service: str,
            data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            self.assertTrue(blocking)
            entity_id = str(data["entity_id"])
            states[entity_id] = SimpleNamespace(
                state="on",
                attributes={"restored": True},
                last_updated=datetime.now(timezone.utc),
            )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=0
            ),
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertFalse(receipt["accepted"])
        self.assertEqual("power_source_unavailable", receipt["error"])
        self.assertEqual(
            ["switch.wall"],
            [
                current.args[2]["entity_id"]
                for current in self.hass.services.async_call.await_args_list
            ],
        )

    async def test_auto_dependency_reuses_fresh_confirmed_source(self) -> None:
        observed = datetime.now(timezone.utc)
        states = {
            "light.living_room": SimpleNamespace(
                state="off", attributes={}, last_updated=observed
            ),
            "switch.wall": SimpleNamespace(
                state="on", attributes={}, last_updated=observed
            ),
        }
        self.hass.states = SimpleNamespace(get=states.get)

        async def apply_service(
            _domain: str,
            service: str,
            data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            self.assertTrue(blocking)
            if service == "turn_on":
                states[str(data["entity_id"])] = SimpleNamespace(
                    state="on",
                    attributes={},
                    last_updated=datetime.now(timezone.utc) + timedelta(seconds=1),
                )

        self.hass.services.async_call.side_effect = apply_service
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=0
            ),
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertTrue(receipt["confirmed"])
        self.assertFalse(receipt["power_precondition"]["sourceTurnedOn"])
        self.assertIsNotNone(
            receipt["power_precondition"]["sourceEvidenceRevision"]
        )
        self.assertEqual(
            ["light.living_room"],
            [
                current.args[2]["entity_id"]
                for current in self.hass.services.async_call.await_args_list
            ],
        )

    async def test_auto_dependency_fails_closed_when_source_stays_off(self) -> None:
        self.hass.states = SimpleNamespace(
            get={
                "light.living_room": SimpleNamespace(state="off", attributes={}),
                "switch.wall": SimpleNamespace(state="off", attributes={}),
            }.get
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=0
            ),
        )

        receipt = await executor.async_execute_device_action("device_1", "turn_on")

        self.assertFalse(receipt["accepted"])
        self.assertEqual("power_source_unavailable", receipt["error"])
        self.hass.services.async_call.assert_awaited_once_with(
            "switch",
            "turn_on",
            {"entity_id": "switch.wall"},
            blocking=True,
        )

    async def test_auto_dependency_dry_run_plans_without_service_call(self) -> None:
        self.hass.states = SimpleNamespace(
            get={
                "light.living_room": SimpleNamespace(state="off", attributes={}),
                "switch.wall": SimpleNamespace(state="off", attributes={}),
            }.get
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=lambda: _power_link(
                policy="auto_turn_on", warmup_seconds=5
            ),
        )

        receipt = await executor.async_execute_device_action(
            "device_1", "turn_on", dry_run=True
        )

        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["dryRun"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_command_guard_distinguishes_manual_and_automatic_action(self) -> None:
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            readback_window_seconds=0.02,
            readback_interval_seconds=0.01,
            command_guard=lambda _entity, action, automatic: (
                "automatic_water_open_forbidden"
                if automatic and action == "turn_on"
                else None
            ),
        )

        manual = await executor.async_execute_device_action("fan_1", "turn_on")
        self.assertTrue(manual["accepted"])
        self.hass.services.async_call.reset_mock()

        automatic = await executor.async_execute(
            _definition(
                (
                    ScenarioAction(
                        id="a1",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id="fan_1",
                        action_id="turn_on",
                    ),
                )
            ),
            "run-water-open",
            scenario_id="water-open",
        )

        self.assertEqual("failed", automatic["status"])
        self.assertEqual(
            "automatic_water_open_forbidden",
            automatic["receipts"][0]["error"],
        )
        self.hass.services.async_call.assert_not_awaited()

    async def test_unpowered_turn_off_is_already_effectively_off(self) -> None:
        self.hass.states = SimpleNamespace(
            get=lambda entity_id: {
                "light.living_room": SimpleNamespace(
                    state="unavailable", attributes={}
                ),
                "switch.wall": SimpleNamespace(state="off", attributes={}),
            }.get(entity_id)
        )
        executor = ScenarioExecutor(
            self.hass,
            self.catalog,
            self.executor._run_callback,
            power_dependency_resolver=_power_link,
        )

        live = await executor.async_execute_device_action("device_1", "turn_off")
        shadow = await executor.async_execute(
            _definition(
                (
                    ScenarioAction(
                        id="a1",
                        type=ScenarioActionType.DEVICE_ACTION,
                        target_id="device_1",
                        action_id="turn_off",
                    ),
                ),
                command_mode=ScenarioCommandMode.SHADOW,
            ),
            "run-shadow-off",
            scenario_id="shadow-off",
        )

        self.assertTrue(live["accepted"])
        self.assertTrue(live["confirmed"])
        self.assertFalse(live["readBack"]["attempted"])
        self.assertTrue(live["readBack"]["matched"])
        self.assertEqual("completed", shadow["status"])
        self.assertTrue(shadow["receipts"][0]["planned"])
        self.assertIsNone(shadow["receipts"][0]["confirmed"])
        self.assertEqual("already_effectively_off", shadow["receipts"][0]["reason"])
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
            power_dependency_resolver=_power_link,
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
            power_dependency_resolver=_power_link,
        )
        definition = _definition(
            (
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
            )
        )

        result = await executor.async_execute(
            definition,
            "run-1",
            scenario_id="sc-1",
            dry_run=True,
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", result["receipts"][1]["status"])
        self.assertEqual(
            25, result["receipts"][1]["adaptive_brightness"]["minimum_percent"]
        )
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
                    type=ScenarioActionType.DELAY,
                    delay_seconds=1,
                ),
                ScenarioAction(
                    id="a3",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
            )
        )

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

    async def test_scenario_confirms_multiple_devices_in_one_shared_window(
        self,
    ) -> None:
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
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="climate_1",
                    action_id="set_temperature",
                    value=22,
                ),
            )
        )
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "climate",
            "set_temperature",
            {"entity_id": "climate.living_room", "temperature": 22},
            blocking=True,
        )

    async def test_number_action_uses_selected_value_and_confirms_read_back(
        self,
    ) -> None:
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
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness",
                    value=128,
                ),
            )
        )
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "brightness": 128},
            blocking=True,
        )

    async def test_normalized_brightness_value_is_confirmed_against_read_back(
        self,
    ) -> None:
        self.hass.states.get = lambda entity_id: SimpleNamespace(
            state="on", attributes={"brightness": 50}
        )

        receipt = await self.executor.async_execute_device_action(
            "device_1", "set_brightness", "50%"
        )

        self.assertTrue(receipt["confirmed"])
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "brightness": 50},
            blocking=True,
        )

    async def test_brightness_percent_scales_to_native_brightness(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_brightness_percent",
                    value=50,
                ),
            )
        )
        await self.executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.hass.services.async_call.assert_awaited_once_with(
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "brightness": 128},
            blocking=True,
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
            "light",
            "turn_on",
            {"entity_id": "light.living_room", "brightness": 128},
            blocking=True,
        )

    async def test_color_temperature_action_uses_kelvin_parameter(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="set_color_temperature",
                    value=3000,
                ),
            )
        )
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

    async def test_adaptive_brightness_uses_solar_curve_and_minimum_percent(
        self,
    ) -> None:
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
        now = datetime(2026, 8, 14, 22, 0, tzinfo=timezone(timedelta(hours=6)))

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

    def test_night_light_and_rgb_values_are_strictly_normalized(self) -> None:
        self.assertEqual(
            26,
            _normalize_light_action_value("set_night_light", "brightness", 10),
        )
        self.assertEqual(
            [255, 179, 107],
            _normalize_light_action_value("set_rgb_color", "rgb_color", "#FFB36B"),
        )
        with self.assertRaises(ValueError):
            _normalize_light_action_value("set_night_light", "brightness", 0)
        with self.assertRaises(ValueError):
            _normalize_light_action_value("set_rgb_color", "rgb_color", "orange")

    async def test_delay_action_receipt(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=1,
                ),
            )
        )
        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="sc-1"
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["receipts"][0]["status"], "completed")
        self.assertEqual(result["receipts"][0]["delay_seconds"], 1)

    async def test_run_scenario_action_calls_callback(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="other",
                ),
            )
        )
        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="sc-1"
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.nested_runs, ["other"])
        self.assertEqual(result["receipts"][0]["nested_run_id"], "nested-other")

    async def test_restart_cancelled_nested_scenario_is_skipped_not_failed(self) -> None:
        async def restarted_callback(
            _scenario_id: str, **_kwargs: object
        ) -> dict[str, Any]:
            return {
                "run_id": "nested-restarted",
                "status": "cancelled",
                "reason": "restarted_by_new_trigger",
                "receipts": [],
            }

        executor = ScenarioExecutor(self.hass, self.catalog, restarted_callback)
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="other",
                ),
            )
        )

        result = await executor.async_execute(
            definition, "run-1", scenario_id="parent"
        )

        self.assertEqual("completed", result["status"])
        self.assertEqual("completed", result["receipts"][0]["status"])
        self.assertTrue(result["receipts"][0]["skipped"])
        self.assertEqual("cancelled", result["receipts"][0]["nested_outcome"])

    async def test_failed_action_runs_matching_planned_turn_off_cleanup(self) -> None:
        light = SimpleNamespace(
            state="off",
            attributes={},
            last_updated=datetime.now(timezone.utc),
        )
        self.hass.state_values["light.living_room"] = light

        async def apply_light(
            _domain: str,
            service: str,
            _data: dict[str, object],
            *,
            blocking: bool,
        ) -> None:
            self.assertTrue(blocking)
            light.state = "on" if service == "turn_on" else "off"
            light.last_updated = datetime.now(timezone.utc)

        self.hass.services.async_call.side_effect = apply_light
        definition = _definition(
            (
                ScenarioAction(
                    id="turn-on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="fails",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="missing-device",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="wait",
                    type=ScenarioActionType.DELAY,
                    delay_seconds=60,
                ),
                ScenarioAction(
                    id="turn-off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="safe-cleanup"
        )

        self.assertEqual("partial", result["status"])
        self.assertEqual(
            ["turn-on", "fails", "turn-off"],
            [receipt["action_id"] for receipt in result["receipts"]],
        )
        self.assertTrue(result["receipts"][-1]["safety_cleanup"])
        self.hass.services.async_call.assert_has_awaits(
            [
                call(
                    "light",
                    "turn_on",
                    {"entity_id": "light.living_room"},
                    blocking=True,
                ),
                call(
                    "light",
                    "turn_off",
                    {"entity_id": "light.living_room"},
                    blocking=True,
                ),
            ]
        )

    async def test_cleanup_does_not_turn_off_device_that_was_already_on(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="turn-on",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="fails",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="missing-device",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="turn-off",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_off",
                ),
            ),
            idempotent_actions=True,
        )

        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="safe-cleanup"
        )

        self.assertEqual("partial", result["status"])
        self.assertEqual(
            ["turn-on", "fails"],
            [receipt["action_id"] for receipt in result["receipts"]],
        )
        self.assertTrue(result["receipts"][0]["skipped"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_run_scenario_dry_run_is_a_successful_plan(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.RUN_SCENARIO,
                    scenario_id="other",
                ),
            )
        )

        result = await self.executor.async_execute(
            definition,
            "run-1",
            scenario_id="sc-1",
            dry_run=True,
        )

        self.assertEqual("completed", result["status"])
        self.assertTrue(result["receipts"][0]["planned"])
        self.assertEqual(["other"], self.nested_runs)

    async def test_notification_action_targets_configured_entity(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Hello",
                ),
            )
        )
        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="sc-1"
        )
        self.assertEqual(result["status"], "completed")
        self.hass.services.async_call.assert_awaited_once_with(
            "notify",
            "mobile_app_tablet",
            {"message": "Hello", "data": {"correlation_id": "run-1"}},
            blocking=True,
        )
        self.assertEqual("run-1", result["receipts"][0]["correlation_id"])

    async def test_notification_uses_safe_default_target(self) -> None:
        executor = ScenarioExecutor(
            self.hass, self.catalog, self.executor._run_callback
        )
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Hello",
                ),
            )
        )
        result = await executor.async_execute(definition, "run-1", scenario_id="sc-1")
        self.assertEqual(result["status"], "completed")
        self.hass.services.async_call.assert_awaited_once_with(
            "notify",
            "notify",
            {"message": "Hello", "data": {"correlation_id": "run-1"}},
            blocking=True,
        )

    async def test_notification_falls_back_to_persistent_notification(self) -> None:
        executor = ScenarioExecutor(
            self.hass, self.catalog, self.executor._run_callback
        )
        self.hass.services.async_call.side_effect = [RuntimeError("missing"), None]
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.NOTIFICATION,
                    message="Hello",
                ),
            )
        )

        result = await executor.async_execute(definition, "run-1", scenario_id="sc-1")

        self.assertEqual("completed", result["status"])
        self.assertEqual(2, self.hass.services.async_call.await_count)
        self.hass.services.async_call.assert_any_await(
            "persistent_notification",
            "create",
            {
                "title": "Hausman",
                "message": "Hello",
                "notification_id": "hausman-scenario-run-1",
            },
            blocking=True,
        )

    async def test_shadow_mode_never_calls_services_or_confirms_state(self) -> None:
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="a2", type=ScenarioActionType.NOTIFICATION, message="План"
                ),
            ),
            command_mode=ScenarioCommandMode.SHADOW,
            idempotent_actions=True,
        )

        result = await self.executor.async_execute(
            definition, "run-shadow-1", scenario_id="shadow-1"
        )

        self.assertEqual("shadow", result["command_mode"])
        self.assertEqual("completed", result["status"])
        self.assertFalse(result["confirmed"])
        self.assertTrue(all(item["planned"] for item in result["receipts"]))
        self.assertTrue(all(item["confirmed"] is None for item in result["receipts"]))
        self.hass.services.async_call.assert_not_awaited()

    async def test_failed_service_stops_execution(self) -> None:
        self.hass.services.async_call.side_effect = RuntimeError("boom")
        definition = _definition(
            (
                ScenarioAction(
                    id="a1",
                    type=ScenarioActionType.DEVICE_ACTION,
                    target_id="device_1",
                    action_id="turn_on",
                ),
                ScenarioAction(
                    id="a2", type=ScenarioActionType.NOTIFICATION, message="x"
                ),
            )
        )
        result = await self.executor.async_execute(
            definition, "run-1", scenario_id="sc-1"
        )
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
        receipt = await self.executor.async_execute_device_action("device_1", "turn_on")

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
        receipt = await self.executor.async_execute_device_action("missing", "turn_on")

        self.assertFalse(receipt["accepted"])
        self.assertFalse(receipt["confirmed"])
        self.assertEqual("failed", receipt["status"])
        self.hass.services.async_call.assert_not_awaited()

    async def test_public_device_action_reports_bounded_unconfirmed_read_back(
        self,
    ) -> None:
        self.hass.states.get = lambda entity_id: SimpleNamespace(
            state="off", attributes={}
        )

        receipt = await self.executor.async_execute_device_action("device_1", "turn_on")

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

    def test_display_device_name_collapses_registry_duplicates(self) -> None:
        # Zigbee2MQTT повторяет имя устройства в friendly_name, а каталог
        # добавляет его же через « · » - в сообщениях остаётся один экземпляр.
        self.assertEqual(
            "Люстра тамбур",
            _display_device_name("Люстра тамбур · Люстра тамбур Люстра тамбур"),
        )
        self.assertEqual(
            "Люстра тамбур",
            _display_device_name("Люстра тамбур Люстра тамбур"),
        )
        self.assertEqual(
            "Датчик протечки · Ванная",
            _display_device_name("Датчик протечки · Ванная"),
        )
        self.assertEqual("Устройство", _display_device_name("Устройство"))

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
