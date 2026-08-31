"""Tests for durable manual ownership of direct Wi-Fi air conditioners."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_manual import (
    DIRECT_WIFI_COMMAND_ATTRIBUTION_MS,
    apply_manual_rooms,
    climate_manual_from_payload,
    climate_manual_to_payload,
    effective_manual_room_ids,
    record_direct_wifi_commands,
    reconcile_climate_manual_memory,
    update_climate_manual_observation,
    update_direct_wifi_observation,
    with_climate_device_mode,
    with_climate_room_mode,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlChannel,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
)
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaHvacMode,
    ClimateHaService,
    ClimateHaServiceCall,
)
from custom_components.hausman_hub.domain.climate_manual import (
    ClimateDirectWifiPhase,
    ClimateManualMemory,
    ClimateManualViolation,
    empty_climate_manual_memory,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateObservationDeviceKind,
    ClimateRoomMode,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from tests.test_contours import setup, source_snapshot
from tests.test_climate_native_runtime import (
    MemoryStore,
    MutableStateView,
    RecordingTrialExecutor,
    healthy_states,
    native_contours,
    native_registry,
)


NOW = 1_800_000_000_000


def _inputs():
    registry, _ = setup()
    registry = replace(
        registry,
        devices=tuple(
            replace(
                device,
                control_channel=ClimateControlChannel.DIRECT_WIFI,
                endpoints=(
                    ClimateEndpoint(
                        ClimateEndpointRole.CONTROL,
                        "climate.living_air_conditioner",
                    ),
                ),
            )
            if device.device_id == "living_air_conditioner"
            else device
            for device in registry.devices
        ),
    )
    observation = build_climate_observation_snapshot(
        registry,
        source_snapshot(),
        observed_at=NOW,
    )
    return registry, observation


def _with_ac_activity(observation, activity: ClimateDeviceActivity):
    return replace(
        observation,
        devices=tuple(
            replace(device, activity=activity)
            if device.device_id == "living_air_conditioner"
            else device
            for device in observation.devices
        ),
    )


class ClimateManualTest(unittest.TestCase):
    def test_hausman_context_provenance_round_trips_and_migrates_v3(self) -> None:
        memory = empty_climate_manual_memory(updated_at=NOW)
        persisted = replace(memory, hausman_context_ids=("hausman.ctx.1", "hausman.ctx.2"))
        payload = climate_manual_to_payload(persisted)
        self.assertEqual(persisted, climate_manual_from_payload(payload))
        legacy = dict(payload)
        legacy["version"] = 3
        legacy.pop("hausman_context_ids")
        migrated = climate_manual_from_payload(legacy)
        self.assertEqual((), migrated.hausman_context_ids)

    def test_hausman_context_provenance_is_bounded(self) -> None:
        memory = empty_climate_manual_memory(updated_at=NOW)
        with self.assertRaises(ClimateManualViolation):
            replace(memory, hausman_context_ids=tuple(f"ctx.{item}" for item in range(129)))

    def test_reconciliation_retains_registered_manual_attribution(self) -> None:
        registry, _ = _inputs()
        memory = with_climate_device_mode(
            empty_climate_manual_memory(updated_at=NOW - 1), registry,
            room_id="living", device_id="living_air_conditioner", manual=True,
            updated_at=NOW,
        )

        reconciled, changed = reconcile_climate_manual_memory(
            memory, registry, now_ms=NOW + 1,
        )

        self.assertFalse(changed)
        self.assertEqual(memory.attributions, reconciled.attributions)

    def test_explicit_external_context_excludes_every_actuator_kind_and_round_trips(self) -> None:
        """Unattributed native facts remain harmless, explicit context is durable."""
        registry, observation = _inputs()
        kinds = (
            ClimateDeviceKind.AIR_CONDITIONER,
            ClimateDeviceKind.HUMIDIFIER,
            ClimateDeviceKind.FLOOR_HEATING,
            ClimateDeviceKind.RADIATOR_THERMOSTAT,
        )
        observed_kinds = (
            ClimateObservationDeviceKind.AIR_CONDITIONER,
            ClimateObservationDeviceKind.HUMIDIFIER,
            ClimateObservationDeviceKind.FLOOR_HEATING,
            ClimateObservationDeviceKind.RADIATOR_THERMOSTAT,
        )
        for kind, observed_kind in zip(kinds, observed_kinds, strict=True):
            configured = replace(
                registry,
                devices=(
                    replace(
                        registry.devices[0], kind=kind, control_channel=None,
                        capabilities=(
                            ClimateCapability.POWER,
                            ClimateCapability.TARGET_TEMPERATURE,
                            ClimateCapability.TARGET_HUMIDITY,
                            ClimateCapability.HVAC_MODE,
                            ClimateCapability.FAN_MODE,
                        ),
                    ),
                    *registry.devices[1:],
                ),
            )
            active = replace(
                observation,
                devices=tuple(
                    replace(item, kind=observed_kind, activity=ClimateDeviceActivity.RUNNING)
                    if item.device_id == "living_air_conditioner" else item
                    for item in observation.devices
                ),
            )
            seeded, _ = update_climate_manual_observation(
                empty_climate_manual_memory(updated_at=NOW - 1), configured, active,
            )
            stopped = replace(
                active, observed_at=NOW + 1,
                devices=tuple(
                    replace(item, activity=ClimateDeviceActivity.STOPPED)
                    if item.device_id == "living_air_conditioner" else item
                    for item in active.devices
                ),
            )
            untouched, _ = update_climate_manual_observation(seeded, configured, stopped)
            if kind is not ClimateDeviceKind.AIR_CONDITIONER:
                self.assertEqual((), untouched.manual_device_ids)
            manual, changed = update_climate_manual_observation(
                seeded, configured, stopped,
                external_device_ids=("living_air_conditioner",),
                context_by_device={"living_air_conditioner": {"context_id": "ha.ctx.1"}},
            )
            self.assertTrue(changed)
            self.assertEqual(("living_air_conditioner",), manual.manual_device_ids)
            self.assertEqual("ha_context", manual.attributions[0].source)
            self.assertEqual(manual, climate_manual_from_payload(climate_manual_to_payload(manual)))

    def test_external_direct_wifi_off_enters_manual_mode(self) -> None:
        registry, observation = _inputs()
        active = _with_ac_activity(observation, ClimateDeviceActivity.COOLING)
        seeded, changed = update_direct_wifi_observation(
            empty_climate_manual_memory(updated_at=NOW - 1),
            registry,
            active,
        )
        stopped = _with_ac_activity(
            replace(observation, observed_at=NOW + 60_000),
            ClimateDeviceActivity.STOPPED,
        )

        manual, changed_again = update_direct_wifi_observation(
            seeded,
            registry,
            stopped,
        )
        projected = apply_manual_rooms(stopped, manual, registry)

        self.assertTrue(changed)
        self.assertTrue(changed_again)
        self.assertEqual((), manual.manual_room_ids)
        self.assertEqual(("living_air_conditioner",), manual.manual_device_ids)
        self.assertIs(projected.room("living").mode, ClimateRoomMode.AUTO)  # type: ignore[union-attr]

    def test_successful_hausmanhub_off_does_not_enter_manual_mode(self) -> None:
        registry, observation = _inputs()
        active = _with_ac_activity(observation, ClimateDeviceActivity.COOLING)
        seeded, _ = update_direct_wifi_observation(
            empty_climate_manual_memory(updated_at=NOW - 1),
            registry,
            active,
        )
        entity_id = registry.device("living_air_conditioner").endpoint(  # type: ignore[union-attr]
            ClimateEndpointRole.CONTROL
        ).entity_id  # type: ignore[union-attr]
        commanded, command_changed = record_direct_wifi_commands(
            seeded,
            registry,
            (
                ClimateHaServiceCall(
                    ClimateHaService.CLIMATE_SET_HVAC_MODE,
                    entity_id,
                    hvac_mode=ClimateHaHvacMode.OFF,
                ),
            ),
            executed_count=1,
            commanded_at=NOW + 1_000,
        )
        stopped = _with_ac_activity(
            replace(observation, observed_at=NOW + 60_000),
            ClimateDeviceActivity.STOPPED,
        )

        updated, _ = update_direct_wifi_observation(
            commanded,
            registry,
            stopped,
        )

        self.assertTrue(command_changed)
        self.assertEqual((), updated.manual_room_ids)
        self.assertEqual((), updated.manual_device_ids)
        self.assertIsNone(
            updated.device("living_air_conditioner").commanded_phase  # type: ignore[union-attr]
        )

    def test_expired_off_intent_does_not_hide_later_manual_shutdown(self) -> None:
        registry, observation = _inputs()
        active = _with_ac_activity(observation, ClimateDeviceActivity.COOLING)
        seeded, _ = update_direct_wifi_observation(
            empty_climate_manual_memory(updated_at=NOW - 1),
            registry,
            active,
        )
        entity_id = registry.device("living_air_conditioner").endpoint(  # type: ignore[union-attr]
            ClimateEndpointRole.CONTROL
        ).entity_id  # type: ignore[union-attr]
        commanded, _ = record_direct_wifi_commands(
            seeded,
            registry,
            (
                ClimateHaServiceCall(
                    ClimateHaService.CLIMATE_SET_HVAC_MODE,
                    entity_id,
                    hvac_mode=ClimateHaHvacMode.OFF,
                ),
            ),
            executed_count=1,
            commanded_at=NOW + 1_000,
        )
        stopped_at = NOW + 1_000 + DIRECT_WIFI_COMMAND_ATTRIBUTION_MS + 1
        stopped = _with_ac_activity(
            replace(observation, observed_at=stopped_at),
            ClimateDeviceActivity.STOPPED,
        )

        updated, _ = update_direct_wifi_observation(
            commanded,
            registry,
            stopped,
        )

        self.assertEqual(("living_air_conditioner",), updated.manual_device_ids)

    def test_ir_and_unavailable_devices_do_not_enter_manual_mode(self) -> None:
        registry, observation = _inputs()
        seeded, _ = update_direct_wifi_observation(
            empty_climate_manual_memory(updated_at=NOW - 1),
            registry,
            _with_ac_activity(observation, ClimateDeviceActivity.COOLING),
        )
        unavailable = replace(
            _with_ac_activity(
                replace(observation, observed_at=NOW + 60_000),
                ClimateDeviceActivity.STOPPED,
            ),
            devices=tuple(
                replace(
                    device,
                    availability=ClimateDeviceAvailability.UNAVAILABLE,
                    activity=ClimateDeviceActivity.UNKNOWN,
                    current_target_temperature=None,
                    current_target_humidity=None,
                    fan_mode=None,
                    quiet=None,
                    last_started_at=None,
                    last_stopped_at=None,
                    cooling_rate_per_hour=None,
                    confirmed_short_cycle_count=None,
                )
                if device.device_id == "living_air_conditioner"
                else device
                for device in observation.devices
            ),
        )
        updated, _ = update_direct_wifi_observation(
            seeded,
            registry,
            unavailable,
        )
        ir_registry = replace(
            registry,
            devices=tuple(
                replace(device, control_channel=ClimateControlChannel.UNIVERSAL_IR)
                if device.kind is ClimateDeviceKind.AIR_CONDITIONER
                else device
                for device in registry.devices
            ),
        )
        ir_updated, _ = update_direct_wifi_observation(
            seeded,
            ir_registry,
            _with_ac_activity(
                replace(observation, observed_at=NOW + 60_000),
                ClimateDeviceActivity.STOPPED,
            ),
        )

        self.assertEqual((), updated.manual_room_ids)
        self.assertEqual((), ir_updated.manual_room_ids)

    def test_explicit_automatic_mode_clears_manual_ownership(self) -> None:
        registry, _ = _inputs()
        manual = ClimateManualMemory(
            updated_at=NOW,
            manual_room_ids=("living",),
            manual_device_ids=(),
            devices=(),
        )

        automatic = with_climate_room_mode(
            manual,
            registry,
            room_id="living",
            manual=False,
            updated_at=NOW + 1,
        )

        self.assertEqual((), automatic.manual_room_ids)

    def test_excluding_primary_sensor_makes_room_effectively_manual(self) -> None:
        registry = native_registry(ClimateControlScope.MANAGED)
        memory = with_climate_device_mode(
            empty_climate_manual_memory(updated_at=NOW - 1),
            registry,
            room_id="living",
            device_id="living_temperature",
            manual=True,
            updated_at=NOW,
        )

        self.assertEqual(("living",), effective_manual_room_ids(memory, registry))

    def test_storage_round_trip_is_strict_and_private_free(self) -> None:
        registry, observation = _inputs()
        memory, _ = update_direct_wifi_observation(
            empty_climate_manual_memory(updated_at=NOW - 1),
            registry,
            _with_ac_activity(observation, ClimateDeviceActivity.COOLING),
        )
        payload = climate_manual_to_payload(memory)

        self.assertEqual(memory, climate_manual_from_payload(payload))
        serialized = json.dumps(asdict(memory), ensure_ascii=False)
        for hidden in ("entity_id", "service", "source_id", "payload"):
            self.assertNotIn(hidden, serialized)
        with self.assertRaises(ClimateManualViolation):
            climate_manual_from_payload({**payload, "extra": True})
        with self.assertRaises(ClimateManualViolation):
            climate_manual_from_payload({**payload, "version": True})


class MemoryManualStore:
    def __init__(self) -> None:
        self.memory: ClimateManualMemory | None = None
        self.saved: list[ClimateManualMemory] = []

    async def async_load(self) -> ClimateManualMemory | None:
        return self.memory

    async def async_save(self, memory: ClimateManualMemory) -> None:
        self.memory = memory
        self.saved.append(memory)


class ClimateManualRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_entity_mode_updates_dashboard_ownership_without_command(self) -> None:
        registry = native_registry(ClimateControlScope.MANAGED)
        store = MemoryManualStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=SafeConfiguration(
                mode="shadow",
                climate_bridge_mode=ClimateControlMode.MANAGED,
                climate_canary_room_id=None,
            ),
            registry_store=MemoryStore(registry),
            contour_store=MemoryStore(native_contours()),
            manual_store=store,
            strict_ha_call_executor=RecordingTrialExecutor(),
            ha_state_view=MutableStateView(healthy_states()),
            now_ms=lambda: NOW,
        )
        await runtime.async_start()

        automatic = await runtime.async_dashboard_climate_ownership()
        changed = await runtime.async_set_device_mode_for_entity(
            "climate.living_ac", "manual"
        )
        manual = await runtime.async_dashboard_climate_ownership()

        self.assertEqual("automatic", automatic["rooms"]["living"])
        self.assertEqual("automatic", automatic["entities"]["climate.living_ac"])
        self.assertEqual("automatic", changed["previous_mode"])
        self.assertEqual("manual", changed["mode"])
        self.assertTrue(changed["changed"])
        self.assertEqual("manual", manual["entities"]["climate.living_ac"])
        self.assertEqual(("living_ac",), store.memory.manual_device_ids)  # type: ignore[union-attr]

    async def test_external_off_stops_managed_commands_and_survives_restart(
        self,
    ) -> None:
        registry = native_registry(ClimateControlScope.MANAGED)
        registry = replace(
            registry,
            devices=tuple(
                replace(device, control_channel=ClimateControlChannel.DIRECT_WIFI)
                if device.device_id == "living_ac"
                else device
                for device in registry.devices
            ),
        )
        view = MutableStateView(healthy_states())
        executor = RecordingTrialExecutor()
        store = MemoryManualStore()
        clock = [NOW]

        def build_runtime() -> ClimateRuntime:
            return ClimateRuntime(
                entry_id="entry",
                configuration=SafeConfiguration(
                    mode="shadow",
                    climate_bridge_mode=ClimateControlMode.MANAGED,
                    climate_canary_room_id=None,
                ),
                registry_store=MemoryStore(registry),
                contour_store=MemoryStore(native_contours()),
                manual_store=store,
                strict_ha_call_executor=executor,
                ha_state_view=view,
                now_ms=lambda: clock[0],
            )

        runtime = build_runtime()
        await runtime.async_start()
        first = await runtime.async_public_snapshot()
        self.assertEqual("automatic", first["rooms"][0]["mode"])
        current = view._states["climate.living_ac"]
        view._states[current.entity_id] = replace(
            current,
            state="off",
            attributes={**current.attributes, "hvac_action": "off"},
            last_updated_ms=NOW + 60_000,
        )
        clock[0] += 60_000

        await runtime.async_run_climate_managed()
        manual = await runtime.async_public_snapshot()

        self.assertEqual([], executor.calls)
        self.assertEqual("automatic", manual["rooms"][0]["mode"])
        self.assertEqual("manual", manual["rooms"][0]["devices"][0]["mode"])
        self.assertEqual(("living_ac",), store.memory.manual_device_ids)  # type: ignore[union-attr]

        restarted = build_runtime()
        await restarted.async_start()
        after_restart = await restarted.async_public_snapshot()
        self.assertEqual("manual", after_restart["rooms"][0]["devices"][0]["mode"])

        await restarted.async_set_device_mode(
            "living", "living_ac", "automatic"
        )
        automatic = await restarted.async_public_snapshot()
        self.assertEqual("automatic", automatic["rooms"][0]["devices"][0]["mode"])

        await restarted.async_set_device_mode(
            "living", "living_temperature", "manual"
        )
        critical_sensor_manual = await restarted.async_public_snapshot()
        self.assertEqual("manual", critical_sensor_manual["rooms"][0]["mode"])
        sensor = next(
            device
            for device in critical_sensor_manual["rooms"][0]["devices"]
            if device["id"] == "living_temperature"
        )
        self.assertEqual("manual", sensor["mode"])
        self.assertEqual(
            ["set_room_mode"],
            critical_sensor_manual["rooms"][0]["control"]["allowed_actions"],
        )


if __name__ == "__main__":
    unittest.main()
