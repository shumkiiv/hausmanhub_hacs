"""Tests for the strict Home Assistant climate call adapters."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_ha_adapters import (
    build_climate_ha_call_plan,
)
from tests.climate_bridge_fixture import (
    import_climate_state,
)
from custom_components.hausman_hub.application.climate_isolation import (
    build_isolated_climate_policy_snapshot,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
    climate_reference_observation,
)
from custom_components.hausman_hub.application.climate_policy import (
    climate_reference_policy,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
)
from custom_components.hausman_hub.domain.climate_demand import ClimateDemandState
from custom_components.hausman_hub.domain.climate_equipment import (
    ClimateDevicePlan,
    ClimateEquipmentAction,
    ClimateEquipmentReason,
    ClimateRoomEquipmentPlan,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDeviceActivity,
    ClimateDeviceAvailability,
    ClimateDeviceObservation,
    ClimateFanMode,
    ClimateObservationDeviceKind,
    ClimateOccupancyMode,
    ClimateSeason,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.climate_policy import (
    resolve_climate_room_policy,
)
from custom_components.hausman_hub.domain.climate_resolution import (
    ClimateRoomThermalResolution,
    ClimateThermalReason,
    ClimateThermalResolution,
)
from custom_components.hausman_hub.domain.climate_stability import (
    ClimateRoomStabilityPlan,
)
from custom_components.hausman_hub.domain.climate_targets import ClimateStrategy
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDevice,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
    ClimateRoom,
)
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaCallLimit,
    ClimateHaCallPlanSnapshot,
    ClimateHaCallViolation,
    ClimateHaHvacMode,
    ClimateHaService,
    ClimateHaServiceCall,
)
from custom_components.hausman_hub.domain.climate_isolation import (
    ClimateIsolatedRoomResult,
    ClimateIsolationSnapshot,
    ClimateRoomIsolationStatus,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateFanMode,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.contours import ContourMode
from tests.test_contours import source_payload


NOW = 1_800_000_000_000
AC_CAPABILITIES = (
    ClimateCapability.POWER,
    ClimateCapability.TARGET_TEMPERATURE,
    ClimateCapability.HVAC_MODE,
    ClimateCapability.FAN_MODE,
)


def _pipeline(payload, registry, contours, *, mutate_observation=None):
    snapshot = import_climate_state(
        payload,
        now_ms=payload["generatedAt"] + 1_000,  # type: ignore[operator]
    )
    observation = build_climate_observation_snapshot(
        registry,
        snapshot,
        observed_at=NOW,
    )
    if mutate_observation is not None:
        observation = mutate_observation(observation)
    return build_isolated_climate_policy_snapshot(
        contours.contour("climate"),
        observation,
    )


def _setup(*, entity_id: str | None, capabilities=AC_CAPABILITIES, humidifier=False):
    payload = source_payload()
    snapshot = import_climate_state(
        payload,
        now_ms=payload["generatedAt"] + 1_000,  # type: ignore[operator]
    )
    room_ids = ["kids"] if humidifier else ["living"]
    source_ids = (
        ["synthetic-humidifier-source-kids"]
        if humidifier
        else ["synthetic-ac-source-living"]
    )
    registry, contours = build_climate_contour_setup(
        snapshot,
        room_ids=room_ids,
        source_ids=source_ids,
        name="Климат",
        mode="automatic",
        target_temperature=25.0,
        target_humidity=45,
        strategy="normal",
    )
    if entity_id is not None or capabilities != AC_CAPABILITIES:
        device = registry.devices[0]
        rebound = replace(
            device,
            capabilities=capabilities,
            endpoints=(
                (ClimateEndpoint(ClimateEndpointRole.CONTROL, entity_id),)
                if entity_id is not None
                else ()
            ),
        )
        registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=(rebound,),
        )
    return payload, registry, contours


def _close_windows(observation):
    return replace(
        observation,
        rooms=tuple(
            replace(room, window=ClimateWindowState.CLOSED)
            for room in observation.rooms
        ),
    )


def _floor_heating_policy():
    observation = climate_reference_observation("winter_trv_uses_cold_weather_target")
    resolution = ClimateRoomThermalResolution(
        room_id="reference_room",
        season=ClimateSeason.WINTER,
        occupancy=ClimateOccupancyMode.HOME,
        central_heating_on=True,
        heating_demand=ClimateDemandState.REQUIRED,
        cooling_demand=ClimateDemandState.NOT_REQUIRED,
        thermal=ClimateThermalResolution.HEATING,
        reason=ClimateThermalReason.HEATING_REQUIRED,
    )
    floor = ClimateDeviceObservation(
        device_id="reference_floor",
        name="Пол",
        room_id="reference_room",
        kind=ClimateObservationDeviceKind.FLOOR_HEATING,
        availability=ClimateDeviceAvailability.AVAILABLE,
        activity=ClimateDeviceActivity.UNKNOWN,
    )
    equipment = ClimateRoomEquipmentPlan(
        room_id="reference_room",
        thermal=resolution.thermal,
        devices=(
            ClimateDevicePlan(
                device_id="reference_floor",
                room_id="reference_room",
                kind=floor.kind,
                availability=floor.availability,
                activity=floor.activity,
                room_data_status=observation.rooms[0].data_status,
                thermal=resolution.thermal,
                season=resolution.season,
                period=observation.home.period,
                occupancy=resolution.occupancy,
                central_heating_on=True,
                central_heating_configured=True,
                outdoor_temperature=observation.home.outdoor_temperature,
                heat_load_temperature=observation.home.heat_load_temperature,
                comfort_temperature=19.5,
                strategy=ClimateStrategy.NORMAL,
                observed_at=observation.observed_at,
                action=ClimateEquipmentAction.SET_TEMPERATURE,
                target_temperature=19.5,
                fan_mode=None,
                quiet=None,
                reason=ClimateEquipmentReason.HEATING_REQUIRED,
            ),
        ),
    )
    stability = ClimateRoomStabilityPlan(room_id="reference_room", devices=())
    return resolve_climate_room_policy(
        observation.rooms[0],
        observation.home,
        observation.control,
        resolution,
        equipment,
        stability,
        (floor,),
        observed_at=observation.observed_at,
    )


class ClimateHaAdapterTest(unittest.TestCase):
    def test_safe_stop_translates_to_one_strict_off_call(self) -> None:
        payload, registry, contours = _setup(entity_id="climate.living_ac")
        isolation = _pipeline(payload, registry, contours)

        plan = build_climate_ha_call_plan(registry, isolation)

        self.assertFalse(plan.commands_enabled)
        self.assertEqual(1, plan.call_count)
        (device,) = plan.room("living").devices  # type: ignore[union-attr]
        self.assertEqual((), device.limits)
        (call,) = device.calls
        self.assertIs(call.service, ClimateHaService.CLIMATE_SET_HVAC_MODE)
        self.assertIs(call.hvac_mode, ClimateHaHvacMode.OFF)
        self.assertEqual("climate.living_ac", call.entity_id)
        self.assertIsNone(call.temperature)
        self.assertIsNone(call.fan_mode)

    def test_cooling_translates_mode_temperature_and_fan_in_order(self) -> None:
        payload, registry, contours = _setup(entity_id="climate.living_ac")
        isolation = _pipeline(
            payload,
            registry,
            contours,
            mutate_observation=_close_windows,
        )

        plan = build_climate_ha_call_plan(registry, isolation)

        (device,) = plan.room("living").devices  # type: ignore[union-attr]
        self.assertEqual(
            (ClimateHaCallLimit.QUIET_NOT_TRANSLATED,),
            device.limits,
        )
        services = tuple(call.service for call in device.calls)
        self.assertEqual(
            (
                ClimateHaService.CLIMATE_SET_HVAC_MODE,
                ClimateHaService.CLIMATE_SET_TEMPERATURE,
                ClimateHaService.CLIMATE_SET_FAN_MODE,
            ),
            services,
        )
        self.assertIs(device.calls[0].hvac_mode, ClimateHaHvacMode.COOL)
        self.assertEqual(26.0, device.calls[1].temperature)
        self.assertIs(device.calls[2].fan_mode, ClimateFanMode.LOW)
        self.assertTrue(
            all(call.entity_id == "climate.living_ac" for call in device.calls)
        )

    def test_module_managed_device_without_endpoint_stays_call_free(self) -> None:
        payload, registry, contours = _setup(entity_id=None)
        isolation = _pipeline(payload, registry, contours)

        plan = build_climate_ha_call_plan(registry, isolation)

        self.assertEqual(0, plan.call_count)
        (device,) = plan.room("living").devices  # type: ignore[union-attr]
        self.assertEqual(
            (ClimateHaCallLimit.MISSING_CONTROL_ENDPOINT,),
            device.limits,
        )

    def test_missing_fan_capability_blocks_the_whole_translation(self) -> None:
        payload, registry, contours = _setup(
            entity_id="climate.living_ac",
            capabilities=tuple(
                value for value in AC_CAPABILITIES if value is not ClimateCapability.FAN_MODE
            ),
        )
        isolation = _pipeline(
            payload,
            registry,
            contours,
            mutate_observation=_close_windows,
        )

        plan = build_climate_ha_call_plan(registry, isolation)

        (device,) = plan.room("living").devices  # type: ignore[union-attr]
        self.assertEqual((), device.calls)
        self.assertEqual(
            (
                ClimateHaCallLimit.MISSING_CAPABILITY,
                ClimateHaCallLimit.QUIET_NOT_TRANSLATED,
            ),
            device.limits,
        )

    def test_missing_hvac_capability_blocks_the_whole_translation(self) -> None:
        payload, registry, contours = _setup(
            entity_id="climate.living_ac",
            capabilities=tuple(
                value for value in AC_CAPABILITIES if value is not ClimateCapability.HVAC_MODE
            ),
        )
        isolation = _pipeline(
            payload,
            registry,
            contours,
            mutate_observation=_close_windows,
        )

        plan = build_climate_ha_call_plan(registry, isolation)

        (device,) = plan.room("living").devices  # type: ignore[union-attr]
        self.assertEqual((), device.calls)
        self.assertIn(ClimateHaCallLimit.MISSING_CAPABILITY, device.limits)

    def test_floor_heating_translates_only_the_temperature_call(self) -> None:
        policy = _floor_heating_policy()
        registry = ClimateRegistry(
            rooms=(ClimateRoom(room_id="reference_room", name="Эталон"),),
            devices=(
                ClimateDevice(
                    device_id="reference_floor",
                    name="Пол",
                    room_id="reference_room",
                    kind=ClimateDeviceKind.FLOOR_HEATING,
                    source_id="reference-floor-source",
                    control_scope=ClimateControlScope.MANAGED,
                    control_owner=ClimateControlOwner.CLIMATE_CORE,
                    capabilities=(
                        ClimateCapability.POWER,
                        ClimateCapability.TARGET_TEMPERATURE,
                    ),
                    endpoints=(
                        ClimateEndpoint(
                            ClimateEndpointRole.CONTROL,
                            "climate.reference_floor",
                        ),
                    ),
                ),
            ),
        )
        isolation = ClimateIsolationSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=policy.observed_at,
            rooms=(
                ClimateIsolatedRoomResult(
                    room_id="reference_room",
                    status=ClimateRoomIsolationStatus.READY,
                    reasons=(),
                    failed_device_ids=(),
                    policy=policy,
                ),
            ),
        )

        plan = build_climate_ha_call_plan(registry, isolation)

        (device,) = plan.room("reference_room").devices  # type: ignore[union-attr]
        (call,) = device.calls
        self.assertIs(call.service, ClimateHaService.CLIMATE_SET_TEMPERATURE)
        self.assertEqual(19.5, call.temperature)
        self.assertEqual("climate.reference_floor", call.entity_id)
        self.assertEqual((), device.limits)

    def test_humidifier_translates_only_power_calls(self) -> None:
        payload, registry, contours = _setup(
            entity_id="humidifier.kids",
            capabilities=(
                ClimateCapability.POWER,
                ClimateCapability.TARGET_HUMIDITY,
            ),
            humidifier=True,
        )
        isolation = _pipeline(
            payload,
            registry,
            contours,
            mutate_observation=_close_windows,
        )

        plan = build_climate_ha_call_plan(registry, isolation)

        (device,) = plan.room("kids").devices  # type: ignore[union-attr]
        services = tuple(call.service for call in device.calls)
        self.assertIn(
            services,
            {
                (ClimateHaService.HUMIDIFIER_TURN_ON,),
                (ClimateHaService.HUMIDIFIER_TURN_OFF,),
                (),
            },
        )
        self.assertTrue(
            all(call.entity_id == "humidifier.kids" for call in device.calls)
        )

    def test_thermostat_translates_only_the_temperature_call(self) -> None:
        policy = climate_reference_policy("winter_trv_uses_cold_weather_target")
        registry = ClimateRegistry(
            rooms=(ClimateRoom(room_id="reference_room", name="Эталон"),),
            devices=(
                ClimateDevice(
                    device_id="reference_radiator_thermostat",
                    name="TRV",
                    room_id="reference_room",
                    kind=ClimateDeviceKind.RADIATOR_THERMOSTAT,
                    source_id="reference-trv-source",
                    control_scope=ClimateControlScope.MANAGED,
                    control_owner=ClimateControlOwner.CLIMATE_CORE,
                    capabilities=(ClimateCapability.TARGET_TEMPERATURE,),
                    endpoints=(
                        ClimateEndpoint(
                            ClimateEndpointRole.CONTROL,
                            "climate.reference_trv",
                        ),
                    ),
                ),
            ),
        )
        isolation = ClimateIsolationSnapshot(
            contour_id="climate",
            contour_mode=ContourMode.AUTOMATIC,
            observed_at=policy.observed_at,
            rooms=(
                ClimateIsolatedRoomResult(
                    room_id="reference_room",
                    status=ClimateRoomIsolationStatus.READY,
                    reasons=(),
                    failed_device_ids=(),
                    policy=policy,
                ),
            ),
        )

        plan = build_climate_ha_call_plan(registry, isolation)

        (device,) = plan.room("reference_room").devices  # type: ignore[union-attr]
        (call,) = device.calls
        self.assertIs(call.service, ClimateHaService.CLIMATE_SET_TEMPERATURE)
        self.assertEqual(19.5, call.temperature)
        self.assertEqual("climate.reference_trv", call.entity_id)
        self.assertEqual((), device.limits)


class ClimateHaCallModelTest(unittest.TestCase):
    def test_call_rejects_values_outside_its_service(self) -> None:
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
                entity_id="climate.living_ac",
            )
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_HVAC_MODE,
                entity_id="climate.living_ac",
                hvac_mode=ClimateHaHvacMode.COOL,
                temperature=26.0,
            )
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaServiceCall(
                service=ClimateHaService.HUMIDIFIER_TURN_ON,
                entity_id="humidifier.kids",
                humidity=45,
            )

    def test_call_rejects_unbounded_or_forged_values(self) -> None:
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
                entity_id="climate.living_ac",
                temperature=42.0,
            )
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaServiceCall(
                service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
                entity_id="living_ac",
                temperature=26.0,
            )
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaServiceCall(
                service=ClimateHaService.HUMIDIFIER_SET_HUMIDITY,
                entity_id="humidifier.kids",
                humidity=True,  # type: ignore[arg-type]
            )

    def test_snapshot_rejects_forged_shape(self) -> None:
        with self.assertRaises(ClimateHaCallViolation):
            ClimateHaCallPlanSnapshot(
                contour_id="climate",
                contour_mode=ContourMode.AUTOMATIC,
                observed_at=NOW,
                rooms=(),
                version=True,  # type: ignore[arg-type]
            )

    def test_translation_stays_command_free_and_source_free(self) -> None:
        payload, registry, contours = _setup(entity_id="climate.living_ac")
        isolation = _pipeline(
            payload,
            registry,
            contours,
            mutate_observation=_close_windows,
        )
        plan = build_climate_ha_call_plan(registry, isolation)
        serialized = json.dumps(asdict(plan), ensure_ascii=False)

        self.assertFalse(plan.commands_enabled)
        for hidden in (
            "source_id",
            "backend",
            "payload",
            "command_type",
        ):
            self.assertNotIn(hidden, serialized)


if __name__ == "__main__":
    unittest.main()
