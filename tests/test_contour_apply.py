from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_application import (
    ClimateApplicationDenialReason,
    ClimateApplicationGateStatus,
    ClimateDesiredStateChanges,
    build_climate_application_plan,
)
from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_ha_observations import (
    MAX_NATIVE_STATE_AGE_MS,
)
from custom_components.hausman_hub.application.contour_apply import (
    ClimateControlAction,
    ClimateControlContext,
    ContourApplyViolation,
    _ContourApplyLedger,
    _canonical_receipt_fingerprint,
    _direct_receipt_request_fingerprint,
    ContourApplyRequest,
    _temperature_only_application_contour,
    build_contour_apply_plan,
    local_desired_state_changes,
    parse_contour_apply_request,
)
from custom_components.hausman_hub.application.climate_runtime import (
    _contour_reliability_metadata,
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
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
    ClimateDeviceAvailability,
    ClimateDeviceActivity,
    ClimatePhysicalFeedback,
    ClimateObservationSnapshot,
    ClimateRoomMode,
    ClimateTemperatureQuality,
    ClimateWindowState,
)
from custom_components.hausman_hub.domain.contours import ContourDefinition
from tests.test_contours import source_snapshot


NOW = 1_800_000_000_000


def _restore_reliable_receipt(
    records: list[dict[str, object]],
) -> _ContourApplyLedger:
    ledger = _ContourApplyLedger(
        operation_id_factory=lambda: "e" * 32, now_ms=lambda: NOW + 1,
    )
    ledger.restore(records)
    return ledger


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
    observation = replace(
        observation,
        home=replace(
            observation.home,
            outdoor_temperature=-5.0,
            air_conditioner_outdoor_guard_configured=True,
            central_heating_on=True,
        ),
        rooms=tuple(
            replace(room, window=ClimateWindowState.OPEN)
            if room.room_id == "kids"
            else room
            for room in observation.rooms
        ),
    )
    return native_registry, contour, observation


class NativeClimateApplicationPlannerTest(unittest.TestCase):
    def test_explicit_target_defers_a_structurally_valid_offline_owner(self) -> None:
        """An offline managed owner is retained, saved and never called."""
        registry, contour, observation = _native_inputs()
        observation = replace(
            observation,
            devices=tuple(
                replace(
                    device, availability=ClimateDeviceAvailability.UNAVAILABLE,
                    activity=ClimateDeviceActivity.UNKNOWN,
                    current_target_temperature=None,
                    current_target_humidity=None,
                    physical_feedback=ClimatePhysicalFeedback.UNKNOWN,
                )
                if device.device_id == "living_air_conditioner" else device
                for device in observation.devices
            ),
        )

        plan = build_climate_application_plan(
            contour, registry, ClimateControlMode.MANAGED, observation,
            fingerprint="f" * 64, target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
            explicit_temperature_targets={"living": 25.5},
        )

        self.assertTrue(plan.preflight_permitted)
        self.assertEqual((), plan.strict_calls)
        self.assertEqual((), plan.denial_reasons)
        self.assertEqual(
            ClimateApplicationGateStatus.DEFERRED,
            plan.device_gates[0].status,
        )

    def test_explicit_target_defers_a_known_stopped_owner_without_target(self) -> None:
        """A fresh, known-off thermostat remains a no-call target owner."""
        registry, contour, observation = _native_inputs()
        observation = replace(
            observation,
            devices=tuple(
                replace(
                    device,
                    availability=ClimateDeviceAvailability.AVAILABLE,
                    activity=ClimateDeviceActivity.STOPPED,
                    current_target_temperature=None,
                )
                if device.device_id == "living_air_conditioner" else device
                for device in observation.devices
            ),
        )

        plan = build_climate_application_plan(
            contour, registry, ClimateControlMode.MANAGED, observation,
            fingerprint="f" * 64, target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
            explicit_temperature_targets={"living": 25.5},
        )

        self.assertTrue(plan.preflight_permitted)
        self.assertEqual((), plan.denial_reasons)
        self.assertEqual(
            ClimateApplicationGateStatus.DEFERRED,
            plan.device_gates[0].status,
        )

    def test_plans_complete_whole_contour_after_every_room_passes_preflight(self) -> None:
        registry, contour, observation = _native_inputs()

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateControlMode.MANAGED,
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
            ClimateControlMode.MANAGED,
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
            ClimateControlMode.MANAGED,
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
            ClimateControlMode.MANAGED,
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
            ClimateControlMode.DISABLED,
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
            ClimateControlMode.MANAGED,
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

    def test_explicit_target_keeps_stale_room_gate_and_never_becomes_aligned(self) -> None:
        """Per-device target ownership cannot bypass stale room observation."""

        registry, contour, observation = _native_inputs()
        stale = replace(
            observation,
            rooms=tuple(
                replace(room, data_status=ClimateDataStatus.STALE)
                if room.room_id == "living" else room
                for room in observation.rooms
            ),
        )
        plan = build_climate_application_plan(
            contour, registry, ClimateControlMode.MANAGED, stale,
            fingerprint="9" * 64, target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
            explicit_temperature_targets={"living": 25.0},
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.ROOM_NOT_READY,),
            plan.denial_reasons,
        )
        self.assertEqual((), plan.initially_aligned_room_ids)

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
            ClimateControlMode.MANAGED,
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
            ClimateControlMode.MANAGED,
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

    def test_manual_managed_actuator_denies_the_native_gate_without_calls(self) -> None:
        registry, contour, observation = _native_inputs()
        manual_registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=tuple(
                replace(device, control_owner=ClimateControlOwner.MANUAL)
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
            manual_registry,
            ClimateControlMode.MANAGED,
            observation,
            fingerprint="3" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
            explicit_temperature_targets={"living": 23.5},
        )

        self.assertEqual((), plan.strict_calls)
        self.assertEqual(
            (ClimateApplicationDenialReason.ACTUATOR_NOT_MANAGED,),
            plan.denial_reasons,
        )

    def test_shared_control_endpoint_denies_without_calls(self) -> None:
        registry, contour, observation = _native_inputs()
        actuator = next(
            device
            for device in registry.devices
            if device.room_id == "living"
            and device.kind is ClimateDeviceKind.AIR_CONDITIONER
        )
        clone = replace(
            actuator,
            device_id="living_air_conditioner_clone",
            source_id="synthetic-living-air-conditioner-clone",
        )
        shared_registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=(*registry.devices, clone),
        )
        shared_contour = replace(
            contour,
            rooms=tuple(
                replace(room, device_ids=(*room.device_ids, clone.device_id))
                if room.room_id == "living"
                else room
                for room in contour.rooms
            ),
        )

        plan = build_climate_application_plan(
            shared_contour,
            shared_registry,
            ClimateControlMode.MANAGED,
            observation,
            fingerprint="4" * 64,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
            explicit_temperature_targets={"living": 23.5},
        )

        self.assertEqual((), plan.strict_calls)
        self.assertIn(
            ClimateApplicationDenialReason.TRANSLATION_INCOMPLETE,
            plan.denial_reasons,
        )

    def test_retains_fingerprint_and_local_desired_state_counts(self) -> None:
        registry, contour, observation = _native_inputs()

        plan = build_climate_application_plan(
            contour,
            registry,
            ClimateControlMode.MANAGED,
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
            ClimateControlMode.MANAGED,
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

    def test_temperature_plan_excludes_an_unrelated_humidifier(self) -> None:
        registry, contour, _ = _native_inputs()
        living = next(room for room in contour.rooms if room.room_id == "living")
        humidifier = next(
            device
            for device in registry.devices
            if device.kind is ClimateDeviceKind.HUMIDIFIER
        )
        moved_humidifier = replace(humidifier, room_id="living")
        registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=tuple(
                moved_humidifier if device.device_id == humidifier.device_id else device
                for device in registry.devices
            ),
        )
        contour = replace(
            contour,
            rooms=(
                replace(
                    living,
                    device_ids=(*living.device_ids, humidifier.device_id),
                ),
            ),
        )

        scoped = _temperature_only_application_contour(
            contour,
            registry,
            target_room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
        )

        self.assertEqual(living.device_ids, scoped.rooms[0].device_ids)

    def test_explicit_temperature_target_covers_all_temperature_actuators_in_a_mixed_room(self) -> None:
        """An explicit temperature target covers AC, floor heat and radiator."""

        registry, contour, observation = _native_inputs()
        air_conditioner = next(
            device
            for device in registry.devices
            if (
                device.room_id == "living"
                and device.kind is ClimateDeviceKind.AIR_CONDITIONER
            )
        )
        floor = replace(
            air_conditioner,
            device_id="living_floor",
            name="Living floor",
            kind=ClimateDeviceKind.FLOOR_HEATING,
            source_id="synthetic-floor-source-living",
            endpoints=(
                ClimateEndpoint(ClimateEndpointRole.CONTROL, "climate.living_floor"),
            ),
        )
        radiator = replace(
            air_conditioner,
            device_id="living_radiator",
            name="Living radiator",
            kind=ClimateDeviceKind.RADIATOR_THERMOSTAT,
            source_id="synthetic-radiator-source-living",
            endpoints=(
                ClimateEndpoint(ClimateEndpointRole.CONTROL, "climate.living_radiator"),
            ),
        )
        registry = ClimateRegistry(
            rooms=registry.rooms,
            devices=(*registry.devices, floor, radiator),
        )
        contour = replace(
            contour,
            rooms=tuple(
                replace(
                    room,
                    device_ids=(
                        *room.device_ids,
                        floor.device_id,
                        radiator.device_id,
                    ),
                )
                if room.room_id == "living"
                else room
                for room in contour.rooms
            ),
        )
        air_conditioner_observation = next(
            device
            for device in observation.devices
            if device.device_id == air_conditioner.device_id
        )
        observation = replace(
            observation,
            devices=(
                *(
                    replace(
                        item,
                        activity=ClimateDeviceActivity.COOLING,
                        current_target_temperature=24.0,
                    )
                    if item.device_id == air_conditioner.device_id else item
                    for item in observation.devices
                ),
                replace(
                    air_conditioner_observation,
                    device_id=floor.device_id,
                    name=floor.name,
                    activity=ClimateDeviceActivity.HEATING,
                    current_target_temperature=24.0,
                ),
                replace(
                    air_conditioner_observation,
                    device_id=radiator.device_id,
                    name=radiator.name,
                    activity=ClimateDeviceActivity.HEATING,
                    current_target_temperature=24.0,
                ),
            ),
        )

        same_target = build_contour_apply_plan(
            contour,
            registry,
            ClimateControlMode.MANAGED,
            observation,
            room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
            explicit_temperature_alignment=True,
            explicit_temperature_targets={"living": 25.0},
        )

        self.assertEqual(
            (
                air_conditioner.device_id,
                floor.device_id,
                radiator.device_id,
            ),
            tuple(call.owner_device_id for call in same_target.strict_calls),
        )

        aligned_observation = replace(
            observation,
            devices=tuple(
                replace(
                    device,
                    current_target_temperature=25.0,
                    observed_at=NOW,
                )
                if device.device_id in {
                    air_conditioner.device_id,
                    floor.device_id,
                    radiator.device_id,
                }
                else device
                for device in observation.devices
            ),
        )
        aligned = build_contour_apply_plan(
            contour,
            registry,
            ClimateControlMode.MANAGED,
            aligned_observation,
            room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(0, 0, 0),
            explicit_temperature_alignment=True,
            explicit_temperature_targets={"living": 25.0},
        )

        self.assertEqual((), aligned.strict_calls)
        self.assertEqual(("living",), aligned.native_plan.initially_aligned_room_ids)

        context = ClimateControlContext(
            action=ClimateControlAction.SET_TEMPORARY_TEMPERATURE,
            room_id="living",
            target_temperature=25.0,
        )
        request = SimpleNamespace(
            request_id="same-target-aligned",
            correlation_id="same-target-aligned-correlation",
        )
        metadata = _contour_reliability_metadata(
            contour,
            aligned,
            context,
            request,
            aligned_observation,
            expected_control_revision=7,
            resulting_control_revision=8,
        )

        scope = metadata["resolved_scope"]
        expected_device_ids = [
            air_conditioner.device_id,
            floor.device_id,
            radiator.device_id,
        ]
        self.assertEqual(expected_device_ids, scope["device_ids"])
        self.assertEqual(
            [{"room_id": "living", "device_ids": expected_device_ids}],
            scope["devices_by_room"],
        )
        self.assertEqual(
            set(expected_device_ids),
            set(metadata["desired_snapshot"]),
        )

        ledger = _ContourApplyLedger(
            operation_id_factory=lambda: "f" * 32,
            now_ms=lambda: NOW,
        )
        record = ledger.begin(
            "same-target-aligned",
            aligned,
            context,
            request.correlation_id,
            enhanced=metadata,
        )
        payload = record.receipt.as_payload()
        observed_at = aligned_observation.observed_at
        for device_id in expected_device_ids:
            leaf = payload["outcomes"]["rooms"]["living"]["devices"][device_id]
            self.assertEqual("confirmed", leaf["status"])
            self.assertEqual("already_in_sync", leaf["execution_state"])
            self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
            self.assertEqual(observed_at, leaf["evidence"]["observed_at"])
            self.assertEqual(25.0, leaf["evidence"]["reported_target_temperature"])
            self.assertEqual(
                25.0,
                leaf["evidence"]["observed_actual"]["target_temperature"],
            )
        schema = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "custom_components"
                / "hausman_hub"
                / "contracts"
                / "v1"
                / "climate-control-receipt.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(payload)

        restored = _ContourApplyLedger(
            operation_id_factory=lambda: "e" * 32,
            now_ms=lambda: NOW + 1,
        )
        restored.restore(ledger.serialized())
        replayed = restored.by_request("same-target-aligned")
        self.assertIsNotNone(replayed)
        self.assertEqual(payload, replayed.receipt.as_payload())  # type: ignore[union-attr]

        for label, source_observed_at in (
            ("at-native-freshness-boundary", NOW - MAX_NATIVE_STATE_AGE_MS),
            ("older-than-native-freshness", NOW - 10_000_000),
            ("future-source", NOW + 1),
            ("missing-source", None),
        ):
            with self.subTest(label=label):
                observed = replace(
                    aligned_observation,
                    devices=tuple(
                        replace(device, observed_at=source_observed_at)
                        if device.device_id == air_conditioner.device_id
                        else device
                        for device in aligned_observation.devices
                    ),
                )
                metadata = _contour_reliability_metadata(
                    contour,
                    aligned,
                    context,
                    SimpleNamespace(
                        request_id=f"same-target-{label}",
                        correlation_id=f"same-target-{label}-correlation",
                    ),
                    observed,
                    expected_control_revision=7,
                    resulting_control_revision=8,
                )
                record = _ContourApplyLedger(
                    operation_id_factory=lambda: "a" * 32,
                    now_ms=lambda: NOW,
                ).begin(
                    f"same-target-{label}",
                    aligned,
                    context,
                    f"same-target-{label}-correlation",
                    enhanced=metadata,
                )
                stale_payload = record.receipt.as_payload()
                stale_leaf = stale_payload["outcomes"]["rooms"]["living"]["devices"][
                    air_conditioner.device_id
                ]
                if label == "at-native-freshness-boundary":
                    self.assertEqual("confirmed", stale_payload["status"])
                    self.assertEqual("already_in_sync", stale_leaf["execution_state"])
                    continue
                self.assertEqual("rejected", stale_payload["status"])
                self.assertTrue(stale_payload["final"])
                self.assertEqual("not_attempted", stale_payload["outcomes"]["rooms"]["living"]["status"])
                self.assertEqual("blocked_before_dispatch", stale_leaf["execution_state"])
                self.assertEqual((0, 0), (stale_leaf["command_count"], stale_leaf["accepted_count"]))
                Draft202012Validator(schema).validate(stale_payload)
                stale_ledger = _ContourApplyLedger(
                    operation_id_factory=lambda: "b" * 32,
                    now_ms=lambda: NOW + 1,
                )
                stale_ledger.restore([
                    {
                        "request_id": f"same-target-{label}",
                        "fingerprint": record.plan.fingerprint,
                        "context": {
                            "action": context.action.value,
                            "room_id": context.room_id,
                            "target_temperature": context.target_temperature,
                            "profile": None,
                        },
                        "receipt": stale_payload,
                    }
                ])
                self.assertEqual(
                    stale_payload,
                    stale_ledger.by_request(f"same-target-{label}").receipt.as_payload(),  # type: ignore[union-attr]
                )

    def test_reliable_receipt_restore_binds_every_duplicated_identity(self) -> None:
        """A partial or hybrid reliable receipt is never a replay candidate."""

        registry, contour, observation = _native_inputs()
        context = ClimateControlContext(
            action=ClimateControlAction.SET_TEMPORARY_TEMPERATURE,
            room_id="living",
            target_temperature=25.0,
        )
        plan = build_contour_apply_plan(
            contour,
            registry,
            ClimateControlMode.MANAGED,
            observation,
            room_ids=("living",),
            desired_state_changes=ClimateDesiredStateChanges(1, 0, 0),
            explicit_temperature_alignment=True,
            explicit_temperature_targets={"living": 25.0},
        )
        request = SimpleNamespace(
            request_id="receipt-binding", correlation_id="receipt-binding-correlation",
        )
        metadata = _contour_reliability_metadata(
            contour, plan, context, request, observation,
            expected_control_revision=7, resulting_control_revision=8,
        )
        ledger = _ContourApplyLedger(
            operation_id_factory=lambda: "f" * 32, now_ms=lambda: NOW,
        )
        record = ledger.begin(
            request.request_id, plan, context, request.correlation_id,
            enhanced=metadata,
        )
        stored = ledger.serialized()
        self.assertEqual(
            record.receipt.as_payload(),
            _restore_reliable_receipt(stored).by_request(request.request_id).receipt.as_payload(),  # type: ignore[union-attr]
        )

        def changed(path: tuple[str, ...], value: object) -> list[dict[str, object]]:
            candidate = deepcopy(stored)
            target = candidate[0]["receipt"]
            for key in path[:-1]:
                target = target[key]  # type: ignore[index]
            target[path[-1]] = value  # type: ignore[index]
            return candidate

        mutations = {
            "top fingerprint": changed(("request_fingerprint",), "e" * 64),
            "intent fingerprint": changed(("intent", "request_fingerprint"), "e" * 64),
            "snapshot action": changed(("action_snapshot", "action"), "clear_room_override"),
            "snapshot parameters": changed(("action_snapshot", "parameters"), {}),
            "scope fingerprint": changed(("intent", "scope_fingerprint"), "e" * 64),
            "desired fingerprint": changed(("desired_snapshot_fingerprint",), "e" * 64),
            "intent scope": changed(("intent", "resolved_scope"), {}),
            "intent target": changed(("intent", "desired_target_temperature"), 19.0),
            "receipt context": changed(("action", "target_temperature"), 19.0),
            "unexpected snapshot key": changed(("action_snapshot", "extra"), True),
        }
        missing_intent = deepcopy(stored)
        del missing_intent[0]["receipt"]["intent"]["scope_fingerprint"]
        mutations["missing intent key"] = missing_intent
        desired_target = deepcopy(stored)
        desired_target[0]["receipt"]["desired_snapshot"]["living_air_conditioner"]["target_temperature"] = 19.0
        desired_target[0]["receipt"]["desired_snapshot_fingerprint"] = _canonical_receipt_fingerprint(
            desired_target[0]["receipt"]["desired_snapshot"]
        )
        mutations["desired target with recomputed fingerprint"] = desired_target
        desired_extra = deepcopy(stored)
        desired_extra[0]["receipt"]["desired_snapshot"]["invented_device"] = deepcopy(
            desired_extra[0]["receipt"]["desired_snapshot"]["living_air_conditioner"]
        )
        desired_extra[0]["receipt"]["desired_snapshot_fingerprint"] = _canonical_receipt_fingerprint(
            desired_extra[0]["receipt"]["desired_snapshot"]
        )
        mutations["extra desired device with recomputed fingerprint"] = desired_extra
        desired_missing = deepcopy(stored)
        del desired_missing[0]["receipt"]["desired_snapshot"]["living_air_conditioner"]
        desired_missing[0]["receipt"]["desired_snapshot_fingerprint"] = _canonical_receipt_fingerprint(
            desired_missing[0]["receipt"]["desired_snapshot"]
        )
        mutations["missing desired device with recomputed fingerprint"] = desired_missing
        desired_renamed = deepcopy(stored)
        desired_renamed[0]["receipt"]["desired_snapshot"]["renamed_device"] = (
            desired_renamed[0]["receipt"]["desired_snapshot"].pop("living_air_conditioner")
        )
        desired_renamed[0]["receipt"]["desired_snapshot_fingerprint"] = _canonical_receipt_fingerprint(
            desired_renamed[0]["receipt"]["desired_snapshot"]
        )
        mutations["renamed desired device with recomputed fingerprint"] = desired_renamed
        for label, candidate in mutations.items():
            with self.subTest(label=label):
                with self.assertRaises(ContourApplyViolation):
                    _restore_reliable_receipt(candidate)

        clear_context = ClimateControlContext(
            action=ClimateControlAction.RETURN_TO_SCHEDULE,
            room_id="living", target_temperature=25.0,
        )
        clear_metadata = _contour_reliability_metadata(
            contour, plan, clear_context,
            SimpleNamespace(
                request_id="receipt-clear-binding",
                correlation_id="receipt-clear-binding-correlation",
            ),
            observation, expected_control_revision=8,
            resulting_control_revision=9,
        )
        clear_ledger = _ContourApplyLedger(
            operation_id_factory=lambda: "a" * 32, now_ms=lambda: NOW,
        )
        clear_ledger.begin(
            "receipt-clear-binding", plan, clear_context,
            "receipt-clear-binding-correlation", enhanced=clear_metadata,
        )
        clear_stored = clear_ledger.serialized()
        self.assertEqual(
            clear_ledger.by_request("receipt-clear-binding").receipt.as_payload(),  # type: ignore[union-attr]
            _restore_reliable_receipt(clear_stored).by_request("receipt-clear-binding").receipt.as_payload(),  # type: ignore[union-attr]
        )
        clear_mutation = deepcopy(clear_stored)
        clear_mutation[0]["receipt"]["desired_snapshot"]["living_air_conditioner"]["override_state"] = "active"
        clear_mutation[0]["receipt"]["desired_snapshot_fingerprint"] = _canonical_receipt_fingerprint(
            clear_mutation[0]["receipt"]["desired_snapshot"]
        )
        with self.assertRaises(ContourApplyViolation):
            _restore_reliable_receipt(clear_mutation)

        full = deepcopy(stored)
        full_context = ClimateControlContext(
            action=ClimateControlAction.APPLY_SAVED_SETTINGS,
        )
        full[0]["context"] = {
            "action": full_context.action.value, "room_id": None,
            "target_temperature": None, "profile": None,
        }
        full_receipt = full[0]["receipt"]
        full_scope = full_receipt["action_snapshot"]["resolved_scope"]
        full_scope["room_ids"].append("kids")
        full_receipt["intent"]["resolved_scope"] = deepcopy(full_scope)
        full_receipt["action"] = full_context.as_payload()
        full_receipt["action_snapshot"].update(
            kind="contour_apply", action="apply_saved_settings",
            parameters={"contour_id": "climate", "confirm": True},
        )
        full_receipt["intent"]["desired_target_temperature"] = None
        full_receipt["intent"]["scope_fingerprint"] = _canonical_receipt_fingerprint(full_scope)
        full_fingerprint = _direct_receipt_request_fingerprint(
            request_id=full_receipt["request_id"],
            correlation_id=full_receipt["correlation_id"], context=full_context,
            scope=full_scope, expected_control_revision=7,
        )
        full_receipt["request_fingerprint"] = full_fingerprint
        full_receipt["action_snapshot"]["request_fingerprint"] = full_fingerprint
        full_receipt["intent"]["request_fingerprint"] = full_fingerprint
        self.assertIsNotNone(_restore_reliable_receipt(full).by_request(request.request_id))
        duplicate_row = deepcopy(full)
        duplicate_scope = duplicate_row[0]["receipt"]["action_snapshot"]["resolved_scope"]
        duplicate_scope["devices_by_room"].append(deepcopy(duplicate_scope["devices_by_room"][0]))
        duplicate_row[0]["receipt"]["intent"]["resolved_scope"] = deepcopy(duplicate_scope)
        duplicate_row[0]["receipt"]["intent"]["scope_fingerprint"] = _canonical_receipt_fingerprint(duplicate_scope)
        with self.assertRaises(ContourApplyViolation):
            _restore_reliable_receipt(duplicate_row)


class ContourApplyRequestTest(unittest.TestCase):
    def test_request_requires_exact_explicit_confirmation(self) -> None:
        self.assertEqual(
            ContourApplyRequest("android-1", "climate", None, None),
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

    def test_request_accepts_optional_room_scope(self) -> None:
        self.assertEqual(
            ContourApplyRequest("admin-1", "climate", ("living",), None),
            parse_contour_apply_request(
                {
                    "request_id": "admin-1",
                    "contour_id": "climate",
                    "confirm": True,
                    "room_ids": ["living"],
                }
            ),
        )
        for invalid_scope in (
            "living",
            [],
            ["living", "living"],
            ["Living"],
            ["living", 1],
            ["living"] * 65,
        ):
            with self.subTest(room_ids=invalid_scope), self.assertRaises(
                ContourApplyViolation
            ):
                parse_contour_apply_request(
                    {
                        "request_id": "admin-1",
                        "contour_id": "climate",
                        "confirm": True,
                        "room_ids": invalid_scope,
                    }
                )

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
