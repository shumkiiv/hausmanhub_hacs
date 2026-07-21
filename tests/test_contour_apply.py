from __future__ import annotations

from dataclasses import replace
import unittest

from custom_components.hausman_hub.application.climate_application import (
    ClimateApplicationDenialReason,
    ClimateApplicationGateStatus,
    ClimateDesiredStateChanges,
    build_climate_application_plan,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.contour_apply import (
    ClimateControlAction,
    ClimateControlContext,
    ContourApplyViolation,
    _ContourApplyLedger,
    build_contour_apply_plan,
    local_desired_state_changes,
    parse_contour_apply_request,
)
from custom_components.hausman_hub.application.contour_override import (
    TemporaryTemperatureAction,
    TemporaryTemperatureViolation,
    parse_temporary_temperature_request,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
)
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateControlOwner,
    ClimateControlScope,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateBridgeMode
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceActivity,
    ClimateObservationSnapshot,
    ClimateRoomMode,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.contours import ContourDefinition
from tests.test_contours import source_snapshot


NOW = 1_800_000_000_000


def _native_inputs() -> tuple[
    ClimateRegistry, ContourDefinition, ClimateObservationSnapshot
]:
    snapshot = source_snapshot()
    registry, contours = build_climate_contour_setup(
        snapshot,
        room_ids=["living", "kids"],
        source_ids=[
            "synthetic-ac-source-living",
            "synthetic-humidifier-source-kids",
        ],
        name="Климат",
        mode="automatic",
        target_temperature=25.0,
        target_humidity=45,
        strategy="normal",
    )
    devices = tuple(
        replace(
            device,
            capabilities=tuple(
                dict.fromkeys((*device.capabilities, ClimateCapability.HVAC_MODE))
            ),
            endpoints=(
                ClimateEndpoint(
                    ClimateEndpointRole.CONTROL,
                    (
                        "climate.living_ac"
                        if device.kind is ClimateDeviceKind.AIR_CONDITIONER
                        else "humidifier.kids"
                    ),
                ),
            ),
        )
        for device in registry.devices
    )
    native_registry = ClimateRegistry(rooms=registry.rooms, devices=devices)
    contour = contours.contour("climate")
    if contour is None:
        raise AssertionError("test contour is unavailable")
    observation = build_climate_observation_snapshot(
        native_registry,
        snapshot,
        observed_at=NOW,
    )
    return native_registry, contour, observation


class NativeClimateApplicationPlannerTest(unittest.TestCase):
    def test_plans_complete_whole_contour_after_every_room_passes_preflight(self) -> None:
        registry, contour, observation = _native_inputs()

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateBridgeMode.MANAGED,
            observation,
            fingerprint="a" * 64,
            target_room_ids=("living", "kids"),
            desired_state_changes=ClimateDesiredStateChanges(
                temperature=0,
                strategy=0,
                automatic_mode=0,
            ),
        )

        self.assertEqual(("living", "kids"), plan.target_room_ids)
        self.assertEqual(
            (ClimateApplicationGateStatus.READY, ClimateApplicationGateStatus.ALIGNED),
            tuple(gate.status for gate in plan.room_gates),
        )
        self.assertEqual(("kids",), plan.initially_aligned_room_ids)
        self.assertEqual(1, len(plan.strict_calls))
        self.assertEqual((), plan.denial_reasons)

    def test_denied_whole_contour_clears_every_executable_call(self) -> None:
        registry, contour, observation = _native_inputs()
        broken_devices = tuple(
            replace(device, endpoints=()) if device.room_id == "kids" else device
            for device in registry.devices
        )
        broken_registry = ClimateRegistry(rooms=registry.rooms, devices=broken_devices)

        plan = build_climate_application_plan(
            contour,
            broken_registry,
            ClimateBridgeMode.MANAGED,
            observation,
            fingerprint="b" * 64,
            target_room_ids=("living", "kids"),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.MISSING_CONTROL_ENDPOINT,),
            plan.denial_reasons,
        )
        self.assertEqual(
            ClimateApplicationGateStatus.DENIED,
            plan.room_gates[1].status,
        )

    def test_temporary_scope_ignores_unselected_broken_room(self) -> None:
        registry, contour, observation = _native_inputs()
        broken_devices = tuple(
            replace(device, endpoints=()) if device.room_id == "kids" else device
            for device in registry.devices
        )
        broken_registry = ClimateRegistry(rooms=registry.rooms, devices=broken_devices)

        plan = build_climate_application_plan(
            contour,
            broken_registry,
            ClimateBridgeMode.MANAGED,
            observation,
            fingerprint="c" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
        )

        self.assertEqual(("living",), plan.target_room_ids)
        self.assertEqual(1, len(plan.strict_calls))
        self.assertEqual((), plan.denial_reasons)

    def test_aligned_room_still_requires_complete_strict_translation(self) -> None:
        registry, contour, observation = _native_inputs()
        limited_registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=tuple(
                replace(
                    device,
                    capabilities=tuple(
                        capability
                        for capability in device.capabilities
                        if capability is not ClimateCapability.HVAC_MODE
                    ),
                )
                if device.room_id == "living"
                else device
                for device in registry.devices
            ),
        )
        aligned_observation = replace(
            observation,
            devices=tuple(
                replace(device, activity=ClimateDeviceActivity.STOPPED)
                if device.room_id == "living"
                else device
                for device in observation.devices
            ),
        )

        plan = build_climate_application_plan(
            contour,
            limited_registry,
            ClimateBridgeMode.MANAGED,
            aligned_observation,
            fingerprint="e" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE,),
            plan.denial_reasons,
        )

    def test_non_managed_mode_denies_the_native_gate_without_calls(self) -> None:
        registry, contour, observation = _native_inputs()

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateBridgeMode.SHADOW,
            observation,
            fingerprint="f" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.RUNTIME_NOT_MANAGED,),
            plan.denial_reasons,
        )

    def test_stale_room_denies_the_native_gate_without_calls(self) -> None:
        registry, contour, observation = _native_inputs()
        stale = replace(
            observation,
            rooms=tuple(
                replace(room, data_status=ClimateDataStatus.STALE)
                if room.room_id == "living"
                else room
                for room in observation.rooms
            ),
        )

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateBridgeMode.MANAGED,
            stale,
            fingerprint="0" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.ROOM_NOT_READY,),
            plan.denial_reasons,
        )

    def test_unavailable_room_denies_the_native_gate_without_calls(self) -> None:
        registry, contour, observation = _native_inputs()
        unavailable = replace(
            observation,
            rooms=tuple(
                replace(
                    room,
                    data_status=ClimateDataStatus.UNAVAILABLE,
                    temperature=None,
                    humidity=None,
                    observed_target_temperature=None,
                    hard_off_temperature=None,
                    observed_target_humidity=None,
                    observed_target_strategy=None,
                    temperature_quality=ClimateTemperatureQuality.UNKNOWN,
                    window=ClimateWindowState.UNKNOWN,
                    mode=ClimateRoomMode.UNKNOWN,
                    authority_eligible=False,
                    cooling_allowed=None,
                    heating_allowed=None,
                )
                if room.room_id == "living"
                else room
                for room in observation.rooms
            ),
        )

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateBridgeMode.MANAGED,
            unavailable,
            fingerprint="1" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (
                ClimateApplicationDenialReason.ROOM_NOT_READY,
                ClimateApplicationDenialReason.ROOM_NOT_COMPARABLE,
            ),
            plan.denial_reasons,
        )

    def test_observed_actuator_denies_the_native_gate_without_calls(self) -> None:
        registry, contour, observation = _native_inputs()
        observed_registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=tuple(
                replace(
                    device,
                    control_scope=ClimateControlScope.OBSERVED,
                    control_owner=ClimateControlOwner.OBSERVED,
                )
                if (
                    device.room_id == "living"
                    and device.kind is not ClimateDeviceKind.TEMPERATURE_SENSOR
                )
                else device
                for device in registry.devices
            ),
        )

        plan = build_climate_application_plan(
            contour,
            observed_registry,
            ClimateBridgeMode.MANAGED,
            observation,
            fingerprint="2" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.ACTUATOR_NOT_MANAGED,),
            plan.denial_reasons,
        )

    def test_retains_fingerprint_and_local_desired_state_counts(self) -> None:
        registry, contour, observation = _native_inputs()

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateBridgeMode.MANAGED,
            observation,
            fingerprint="d" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 2, 0),
        )

        self.assertEqual("d" * 64, plan.fingerprint)
        self.assertEqual(
            ClimateDesiredStateChanges(1, 2, 0),
            plan.desired_state_changes,
        )

    def test_contour_apply_stores_native_plan_and_binds_idempotency_to_fingerprint(
        self,
    ) -> None:
        registry, contour, observation = _native_inputs()
        updated = replace(
            contour,
            rooms=tuple(
                replace(
                    room,
                    day_profile=replace(
                        room.day_profile,
                        target_temperature=24.0,
                    ),
                )
                if room.room_id == "living"
                else room
                for room in contour.rooms
            ),
        )
        changes = local_desired_state_changes(
            contour,
            updated,
            target_room_ids=("living",),
        )

        plan = build_contour_apply_plan(
            updated,
            registry,
            ClimateBridgeMode.MANAGED,
            observation,
            room_ids=("living",),
            desired_state_changes=changes,
        )
        ledger = _ContourApplyLedger(
            operation_id_factory=lambda: "f" * 32,
            now_ms=lambda: NOW,
        )
        context = ClimateControlContext(
            action=ClimateControlAction.APPLY_SAVED_SETTINGS,
        )
        record = ledger.begin("native-1", plan, context)

        self.assertEqual(1, record.plan.native_plan.desired_state_changes.temperature)
        self.assertIs(record, ledger.existing("native-1", plan.fingerprint, context))
        with self.assertRaises(ContourApplyViolation):
            ledger.existing("native-1", "e" * 64, context)


class ContourApplyRequestTest(unittest.TestCase):
    def test_request_requires_exact_explicit_confirmation(self) -> None:
        self.assertEqual(
            ("android-1", "climate"),
            parse_contour_apply_request(
                {
                    "request_id": "android-1",
                    "contour_id": "climate",
                    "confirm": True,
                }
            ),
        )
        for invalid in (
            {"request_id": "android-1", "contour_id": "climate"},
            {
                "request_id": "android-1",
                "contour_id": "climate",
                "confirm": False,
            },
            {
                "request_id": "android-1",
                "contour_id": "climate",
                "confirm": True,
                "command": "raw",
            },
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                ContourApplyViolation
            ):
                parse_contour_apply_request(invalid)

    def test_temporary_temperature_request_is_bounded_and_explicit(self) -> None:
        request = parse_temporary_temperature_request(
            {
                "request_id": "temporary-1",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.5,
                "confirm": True,
            }
        )

        self.assertIs(request.action, TemporaryTemperatureAction.SET)
        self.assertEqual(23.5, request.target_temperature)
        clear = parse_temporary_temperature_request(
            {
                "request_id": "temporary-clear-1",
                "contour_id": "climate",
                "room_id": "living",
                "action": "clear",
                "target_temperature": None,
                "confirm": True,
            }
        )
        self.assertIs(clear.action, TemporaryTemperatureAction.CLEAR)
        self.assertIsNone(clear.target_temperature)

        base = {
            "request_id": "temporary-2",
            "contour_id": "climate",
            "room_id": "living",
            "action": "set",
            "target_temperature": 23.5,
            "confirm": True,
        }
        for invalid in (
            {**base, "confirm": False},
            {**base, "target_temperature": 23.2},
            {**base, "target_temperature": 29.0},
            {**base, "duration": 60},
            {**base, "action": "raw"},
            {**base, "action": "clear"},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                TemporaryTemperatureViolation
            ):
                parse_temporary_temperature_request(invalid)


if __name__ == "__main__":
    unittest.main()
