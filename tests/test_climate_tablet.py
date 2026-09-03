"""Tests for the canonical tablet climate projection and durable operations."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application import climate_tablet as climate_tablet_module
from custom_components.hausman_hub.application.climate_tablet import (
    ClimateTabletOperationNotFound,
    ClimateTabletService,
    ClimateTabletUnavailable,
    ClimateTabletViolation,
    climate_tablet_snapshot,
    parse_climate_tablet_action,
)
from custom_components.hausman_hub.application.contour_apply import ContourApplyStatus
from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.climate_ledger_keyring import ClimateLedgerKeyring
from custom_components.hausman_hub.domain.climate import (
    ClimateCapability,
    ClimateDeviceKind,
    ClimateEndpoint,
    ClimateEndpointRole,
    ClimateRegistry,
)
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.climate_ha_calls import ClimateHaService
from tests.test_climate_runtime import (
    MemoryBridge,
    MemoryContourStore,
    MemoryStore,
    ReflectingStrictExecutor,
    build_climate_contour_setup,
    configuration,
    native_application_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "custom_components" / "hausman_hub" / "contracts" / "v1"


def contract_validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def managed_home() -> dict[str, object]:
    payload = json.loads(
        (ROOT / "fixtures" / "hausmanhub_climate_v12" / "home.json").read_text(
            encoding="utf-8"
        )
    )
    contour = payload["contours"][0]
    contour["execution"]["settings_apply"]["available"] = True
    contour["execution"]["temporary_temperature"]["available"] = True
    contour["rooms"][0]["temporary_temperature"]["available"] = True
    room = payload["rooms"][0]
    room["devices"][0]["control_scope"] = "managed"
    room["control"]["enabled"] = True
    room["control"]["allowed_actions"] = [
        "set_room_target",
        "turn_room_off",
    ]
    room["control"]["blocked_reasons"] = []
    for availability in room["control"]["action_availability"].values():
        availability["allowed"] = True
        availability["blocked_reasons"] = []
    return payload


def action_request(
    revision: int,
    *,
    request_id: str = "tablet.climate.0001",
    target: float = 23.5,
) -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
        "request_id": request_id,
        "expected_state_revision": revision,
        "action": "set_room_target",
        "room_id": "living",
        "parameters": {"target_temperature": target},
    }


def legacy_tablet_fingerprint(payload: dict[str, object]) -> str:
    """Exact v1.52.195 operation identity, before reliability fields."""

    return hashlib.sha256(
        json.dumps(
            {
                "expected_state_revision": payload["expected_state_revision"],
                "action": payload["action"],
                "room_id": payload["room_id"],
                "parameters": payload["parameters"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class MemoryOperationStore:
    _scope_bindings_by_payload: dict[str, dict[str, object]] = {}
    reliable_scope_integrity_key = "1" * 64

    def __init__(self, payload: object | None = None) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved: list[dict[str, object]] = []
        self._scope_bindings = copy.deepcopy(
            self._scope_bindings_by_payload.get(self._payload_key(self.payload), {})
        )

    @staticmethod
    def _payload_key(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    async def async_load(self) -> object | None:
        return copy.deepcopy(self.payload)

    async def async_save(self, payload: dict[str, object]) -> None:
        self.payload = copy.deepcopy(payload)
        self.saved.append(copy.deepcopy(payload))
        self._scope_bindings_by_payload[self._payload_key(self.payload)] = (
            copy.deepcopy(self._scope_bindings)
        )

    async def async_load_reliable_scope_bindings(self) -> object | None:
        return copy.deepcopy(self._scope_bindings)

    async def async_save_reliable_scope_bindings(
        self, bindings: dict[str, object]
    ) -> None:
        self._scope_bindings = copy.deepcopy(bindings)
        self._scope_bindings_by_payload[self._payload_key(self.payload)] = (
            copy.deepcopy(self._scope_bindings)
        )


class CrashOnSaveStore(MemoryOperationStore):
    """Deterministic persistence crash used to prove recovery is non-replayable."""

    def __init__(self, fail_on_save: int) -> None:
        super().__init__()
        self._fail_on_save = fail_on_save
        self._save_count = 0

    async def async_save(self, payload: dict[str, object]) -> None:
        self._save_count += 1
        if self._save_count == self._fail_on_save:
            raise RuntimeError("injected storage crash")
        await super().async_save(payload)


class ScopeBindingSaveFailureStore(MemoryOperationStore):
    async def async_save_reliable_scope_bindings(
        self, bindings: dict[str, object]
    ) -> None:
        del bindings
        raise RuntimeError("injected reliable scope binding save failure")


class AuthenticatedLedgerMemoryStore(MemoryOperationStore):
    """Test double for a setup-verified external persistent ledger."""

    authenticated_external_ledger_ready = True
    reliable_scope_integrity_key = ClimateLedgerKeyring(
        active_key_id="test-1",
        keys={"test-1": b"1" * 32},
        source_path=Path("/var/lib/hausman/climate-ledger.json"),
    )


class SharedAuthenticatedLedgerMemoryStore(AuthenticatedLedgerMemoryStore):
    """One test store for tablet and direct-runtime revision ownership."""

    def __init__(self) -> None:
        super().__init__()
        self._direct_control_records: object | None = None
        self._control_revision = 0
        self._revision_lock = asyncio.Lock()

    async def async_load_direct_control(self) -> object | None:
        return copy.deepcopy(self._direct_control_records)

    async def async_save_direct_control(self, records: object) -> None:
        self._direct_control_records = copy.deepcopy(records)

    async def async_current_control_revision(self) -> int:
        async with self._revision_lock:
            return self._control_revision

    async def async_reserve_control_revision(self, expected: int) -> int:
        async with self._revision_lock:
            if expected != self._control_revision:
                raise ValueError("stale climate control revision")
            self._control_revision += 1
            return self._control_revision


class FailingDirectControlCheckpointStore(SharedAuthenticatedLedgerMemoryStore):
    """Inject one native checkpoint failure without hiding saved history."""

    def __init__(
        self,
        *,
        fail_on_direct_save: int | None = None,
        fail_from_direct_save: int | None = None,
    ) -> None:
        super().__init__()
        self.fail_on_direct_save = fail_on_direct_save
        self.fail_from_direct_save = fail_from_direct_save
        self.direct_save_count = 0

    async def async_save_direct_control(self, records: object) -> None:
        self.direct_save_count += 1
        if (
            self.direct_save_count == self.fail_on_direct_save
            or (
                self.fail_from_direct_save is not None
                and self.direct_save_count >= self.fail_from_direct_save
            )
        ):
            raise RuntimeError("injected direct control checkpoint failure")
        await super().async_save_direct_control(records)


def native_home_target_runtime(
    *,
    include_humidifier: bool,
) -> tuple[ClimateRuntime, SharedAuthenticatedLedgerMemoryStore, MemoryContourStore, ReflectingStrictExecutor]:
    """Build the real reserved-action path with complete native actuator scope."""

    store = SharedAuthenticatedLedgerMemoryStore()
    registry, contours = build_climate_contour_setup(
        MemoryBridge().snapshot,
        room_ids=["living"],
        source_ids=["synthetic-ac-source-living"],
        name="Климат",
        mode="automatic",
        target_temperature=25.0,
        target_humidity=45,
        strategy="normal",
    )
    registry, state_view = native_application_inputs(registry)
    air_conditioner = next(
        device
        for device in registry.devices
        if device.kind is ClimateDeviceKind.AIR_CONDITIONER
    )
    extras = (
        replace(
            air_conditioner,
            device_id="living_radiator",
            name="Living radiator",
            kind=ClimateDeviceKind.RADIATOR_THERMOSTAT,
            source_id="synthetic-radiator-source-living",
            endpoints=(ClimateEndpoint(ClimateEndpointRole.CONTROL, "climate.living_radiator"),),
        ),
        replace(
            air_conditioner,
            device_id="living_floor",
            name="Living floor",
            kind=ClimateDeviceKind.FLOOR_HEATING,
            source_id="synthetic-floor-source-living",
            endpoints=(ClimateEndpoint(ClimateEndpointRole.CONTROL, "climate.living_floor"),),
        ),
    )
    if include_humidifier:
        extras += (
            replace(
                air_conditioner,
                device_id="living_humidifier",
                name="Living humidifier",
                kind=ClimateDeviceKind.HUMIDIFIER,
                source_id="synthetic-humidifier-source-living",
                capabilities=(
                    ClimateCapability.POWER,
                    ClimateCapability.TARGET_HUMIDITY,
                ),
                endpoints=(ClimateEndpoint(ClimateEndpointRole.CONTROL, "humidifier.living"),),
            ),
        )
    registry = replace(registry, devices=(*registry.devices, *extras))
    contour = contours.contour("climate")
    if contour is None:
        raise AssertionError("test contour is unavailable")
    contours = replace(
        contours,
        contours=(
            replace(
                contour,
                rooms=(replace(contour.rooms[0], device_ids=(*contour.rooms[0].device_ids, *(device.device_id for device in extras))),),
            ),
        ),
    )
    template_state = state_view.states[air_conditioner.endpoint(ClimateEndpointRole.CONTROL).entity_id]  # type: ignore[union-attr]
    for device in extras:
        endpoint = device.endpoint(ClimateEndpointRole.CONTROL)
        if endpoint is not None:
            state_view.states[endpoint.entity_id] = replace(
                template_state,
                entity_id=endpoint.entity_id,
                state="on" if device.kind is ClimateDeviceKind.HUMIDIFIER else "cool",
                attributes=(
                    {"humidity": 45}
                    if device.kind is ClimateDeviceKind.HUMIDIFIER
                    else dict(template_state.attributes)
                ),
            )
    contour_store = MemoryContourStore(contours)
    executor = ReflectingStrictExecutor(state_view, advance_timestamp=True)
    runtime = ClimateRuntime(
        entry_id="entry",
        configuration=configuration(ClimateControlMode.MANAGED),
        registry_store=MemoryStore(registry),
        contour_store=contour_store,
        strict_ha_call_executor=executor,
        ha_state_view=state_view,
        operation_id_factory=lambda: "a" * 32,
        now_ms=lambda: 1784280005000,
        direct_control_store=store,
    )
    return runtime, store, contour_store, executor


class FailingAuthenticatedLedgerMemoryStore(AuthenticatedLedgerMemoryStore):
    async def async_save(self, payload: dict[str, object]) -> None:
        del payload
        raise RuntimeError("injected authenticated ledger save failure")


class FailingReservationStore(AuthenticatedLedgerMemoryStore):
    async def async_reserve_control_revision(self, expected: int) -> int:
        del expected
        raise OSError("injected reservation backend failure")


class FakeRuntime:
    def __init__(self, home: dict[str, object]) -> None:
        self.home = copy.deepcopy(home)
        self.configuration = SimpleNamespace(
            mode="shadow",
            climate_bridge_mode=ClimateControlMode.MANAGED,
        )
        self.commands: list[dict[str, object]] = []
        self.result_status = ContourApplyStatus.CONFIRMED
        self.recovery_without_readback: set[str] = set()
        self.recovery_private_metadata: dict[tuple[str, str], dict[str, object]] = {}
        self.recovery_error: Exception | None = None
        self.recovery_post_dispatch_error: Exception | None = None

    def _mark_observed(self, now: object | None = None) -> None:
        clock_timestamp = (
            int(now.timestamp() * 1000) + 1
            if isinstance(now, datetime)
            else int(time.time() * 1000) + 1
        )
        previous = [self.home.get("generated_at")]
        for room in self.home["rooms"]:
            for device in room["devices"]:
                previous.append(device.get("observed_at"))
        latest = max(
            (value for value in previous if type(value) is int),
            default=0,
        )
        # A fake execution finishes after its input snapshot.  Keep that
        # causal order even when a test uses a fixed service clock.
        timestamp = max(clock_timestamp, latest + 2)
        self.home["generated_at"] = timestamp
        for room in self.home["rooms"]:
            for device in room["devices"]:
                device["observed_at"] = timestamp

    async def async_public_snapshot(self) -> dict[str, object]:
        return copy.deepcopy(self.home)

    async def async_recovery_private_metadata(self) -> dict[tuple[str, str], dict[str, object]]:
        return copy.deepcopy(self.recovery_private_metadata)

    async def async_temporary_temperature(
        self, payload: object, now: object
    ) -> object:
        self.commands.append(copy.deepcopy(payload))
        if isinstance(payload, dict):
            room = self.home["rooms"][0]
            action = payload.get("action")
            if action == "set":
                target = payload.get("target_temperature")
                for device in room["devices"]:
                    device["reported_target_temperature"] = target
            elif action == "clear":
                room["temporary_override"] = {"active": False}
        self._mark_observed(now)
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_home_climate_targets(self, payload: object) -> object:
        self.commands.append(copy.deepcopy(payload))
        temperature_changes = humidity_changes = 0
        if isinstance(payload, dict):
            for room in self.home["rooms"]:
                for axis in ("target_temperature", "target_humidity"):
                    if axis in payload:
                        if room.get(axis) != payload[axis]:
                            if axis == "target_temperature":
                                temperature_changes += 1
                            else:
                                humidity_changes += 1
                        room[axis] = payload[axis]
                        for device in room["devices"]:
                            device[f"reported_{axis}"] = payload[axis]
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
            temperature_changes=temperature_changes,
            strategy_changes=0,
            automatic_mode_changes=0,
            humidity_changes=humidity_changes,
        )

    async def async_synchronize_climate(self) -> object:
        self.commands.append({"action": "synchronize_home"})
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_room_humidity_target(
        self, *, request_id: object, room_id: object, target_humidity: object
    ) -> object:
        self.commands.append(
            {
                "request_id": request_id,
                "room_id": room_id,
                "target_humidity": target_humidity,
            }
        )
        self.home["rooms"][0]["target_humidity"] = target_humidity
        for device in self.home["rooms"][0]["devices"]:
            device["reported_target_humidity"] = target_humidity
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_set_room_mode(self, room_id: object, mode: object) -> object:
        self.commands.append({"room_id": room_id, "mode": mode})
        self.home["rooms"][0]["mode"] = mode
        for device in self.home["rooms"][0]["devices"]:
            device["mode"] = mode
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_room_minimum_temperature(
        self, *, request_id: object, room_id: object, minimum_temperature: object
    ) -> object:
        self.commands.append({"request_id": request_id, "room_id": room_id, "minimum_temperature": minimum_temperature})
        self.home["rooms"][0]["minimum_temperature"] = minimum_temperature
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_room_target_strategy(
        self, *, request_id: object, room_id: object, target_strategy: object
    ) -> object:
        self.commands.append({"request_id": request_id, "room_id": room_id, "target_strategy": target_strategy})
        self.home["rooms"][0]["target_strategy"] = target_strategy
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_turn_room_off(self, *, request_id: object, room_id: object) -> object:
        self.commands.append({"request_id": request_id, "room_id": room_id, "action": "turn_room_off"})
        for device in self.home["rooms"][0]["devices"]:
            device["state"] = "off"
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_set_device_mode(
        self, room_id: object, device_id: object, mode: object
    ) -> object:
        self.commands.append(
            {"room_id": room_id, "device_id": device_id, "mode": mode}
        )
        next(
            item for item in self.home["rooms"][0]["devices"]
            if item["id"] == device_id
        )["mode"] = mode
        self._mark_observed()
        return SimpleNamespace(
            status=self.result_status,
            command_count=1,
            confirmed_room_count=1,
            accepted_count=1,
        )

    async def async_recover_device(
        self, *, request_id: object, room_id: object, device_id: object,
        desired: object, expected_control_revision: object = None,
    ) -> object:
        if self.recovery_error is not None:
            raise self.recovery_error
        self.commands.append({"request_id": request_id, "room_id": room_id, "device_id": device_id, "desired": copy.deepcopy(desired), "action": "recover"})
        if self.recovery_post_dispatch_error is not None:
            raise self.recovery_post_dispatch_error
        device_state = next(
            item for item in self.home["rooms"][0]["devices"]
            if item["id"] == device_id
        )
        if device_id not in self.recovery_without_readback:
            device_state["mode"] = "automatic"
            device_state["reported_target_temperature"] = desired.get("target_temperature")
            device_state["reported_target_humidity"] = desired.get("target_humidity")
            device_state["observed_at"] = 1_785_949_320_001
        return SimpleNamespace(status=self.result_status, confirmed_room_count=1, accepted_count=1)

    async def async_recover_offline_device(
        self, *, room_id: object, device_id: object, expected_control_revision: object
    ) -> object:
        self.commands.append({"room_id": room_id, "device_id": device_id,
                              "expected_control_revision": expected_control_revision,
                              "action": "recover_offline"})
        next(
            item for item in self.home["rooms"][0]["devices"]
            if item["id"] == device_id
        )["mode"] = "automatic"
        return SimpleNamespace(status=self.result_status, confirmed_room_count=0, accepted_count=0)


class ClimateTabletProjectionTest(unittest.TestCase):
    def test_managed_projection_exposes_only_currently_executable_actions(self) -> None:
        payload = climate_tablet_snapshot(managed_home(), climate_mode="managed")

        self.assertEqual("managed", payload["phase"])
        self.assertEqual("hausman_hub", payload["authority"])
        self.assertTrue(payload["commands_enabled"])
        self.assertEqual(
            "Режим неизвестен",
            payload["rooms"][0]["devices"][0]["mode_name"],
        )

    def test_manual_device_mode_has_russian_label(self) -> None:
        home = managed_home()
        home["rooms"][0]["devices"][0]["mode"] = "manual"

        payload = climate_tablet_snapshot(home, climate_mode="managed")

        self.assertEqual(
            "Ручной режим",
            payload["rooms"][0]["devices"][0]["mode_name"],
        )
        self.assertEqual(
            ["set_home_targets", "synchronize_home"],
            payload["home_control"]["allowed_actions"],
        )
        room = payload["rooms"][0]
        self.assertEqual(["set_room_target", "turn_room_off"], room["control"]["allowed_actions"])

        self.assertEqual("air_conditioner", room["devices"][0]["kind"])
        self.assertEqual("managed", room["devices"][0]["control_scope"])
        self.assertEqual("working", room["devices"][0]["state"])
        self.assertIsNone(room["devices"][0]["cooldown"])
        self.assertEqual(
            {"minimum": 18, "maximum": 28, "step": 0.5},
            room["temperature_range"],
        )
        self.assertEqual("day", room["active_profile"])
        self.assertFalse(room["temporary_override"]["active"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_deviation_guard_reuses_existing_cooldown_surface(self) -> None:
        home = managed_home()
        device = home["rooms"][0]["devices"][0]
        device["cooldown"] = {
            "active": True,
            "remaining_seconds": 120,
            "reason": "rate_limit",
        }
        device["deviation_guard"] = {
            "mode": "enforce",
            "status": "cooldown",
            "expected_state": "off",
            "observed_state": "working",
            "retry_count": 1,
            "max_retries": 3,
            "last_deviation_at": 1_800_000_000_000,
            "next_retry_at": 1_800_000_120_000,
            "escalated_at": None,
        }

        payload = climate_tablet_snapshot(home, climate_mode="managed")
        projected = payload["rooms"][0]["devices"][0]

        self.assertEqual(device["cooldown"], projected["cooldown"])
        self.assertEqual(device["deviation_guard"], projected["deviation_guard"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_typed_home_targets_do_not_depend_on_legacy_settings_apply(self) -> None:
        home = managed_home()
        home["contours"][0]["execution"]["settings_apply"]["available"] = False

        payload = climate_tablet_snapshot(home, climate_mode="managed")

        self.assertEqual(
            ["set_home_targets", "synchronize_home"],
            payload["home_control"]["allowed_actions"],
        )
        self.assertTrue(payload["home_control"]["enabled"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_home_targets_fail_closed_for_every_native_preflight_gap(self) -> None:
        cases: list[tuple[str, callable]] = [
            ("not-managed", lambda home: home["rooms"][0]["devices"][0].update(control_scope="observed")),
            ("stale", lambda home: home["climate"].update(fresh=False)),
            ("registry", lambda home: home["reconciliation"].update(matches=False)),
            ("nonautomatic-contour", lambda home: home["contours"][0].update(mode="manual")),
            ("manual-exclusion", lambda home: home["rooms"][0].update(mode="manual")),
            ("native-plan-incomplete", lambda home: home["rooms"][0]["control"].update(allowed_actions=[])),
        ]
        for name, mutate in cases:
            with self.subTest(name=name):
                home = managed_home()
                mutate(home)
                payload = climate_tablet_snapshot(home, climate_mode="managed")
                self.assertNotIn("set_home_targets", payload["home_control"]["allowed_actions"])

        pending = climate_tablet_snapshot(
            managed_home(), climate_mode="managed",
            active_operations=({"room_id": None, "status": "pending"},),
        )
        self.assertNotIn("set_home_targets", pending["home_control"]["allowed_actions"])

    def test_stale_projection_keeps_manual_exclusion_available(self) -> None:
        home = managed_home()
        home["climate"]["fresh"] = False
        home["rooms"][0]["control"]["allowed_actions"].append("set_room_mode")

        payload = climate_tablet_snapshot(home, climate_mode="managed")

        self.assertTrue(payload["commands_enabled"])
        self.assertEqual([], payload["blocked_reasons"])
        self.assertEqual([], payload["home_control"]["allowed_actions"])
        self.assertEqual(
            ["set_room_mode"], payload["rooms"][0]["control"]["allowed_actions"]
        )
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_shadow_projection_keeps_observations_and_disables_every_action(self) -> None:
        payload = climate_tablet_snapshot(managed_home(), climate_mode="shadow")

        self.assertEqual("shadow", payload["phase"])
        self.assertEqual("legacy_climate_core", payload["authority"])
        self.assertFalse(payload["commands_enabled"])
        self.assertEqual("living", payload["rooms"][0]["id"])
        self.assertEqual(
            [], payload["rooms"][0]["control"]["allowed_actions"]
        )
        self.assertIn(
            "shadow_only", payload["rooms"][0]["control"]["blocked_reasons"]
        )
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_disabled_projection_never_reads_or_invents_room_state(self) -> None:
        payload = climate_tablet_snapshot(
            None,
            climate_mode="disabled",
            generated_at=1_785_949_200_000,
        )

        self.assertEqual("disabled", payload["phase"])
        self.assertEqual("none", payload["authority"])
        self.assertEqual([], payload["rooms"])
        self.assertFalse(payload["commands_enabled"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_disabled_projection_keeps_durable_active_operation_visible(self) -> None:
        operation = {
            "operation_id": "0123456789abcdef0123456789abcdef",
            "request_id": "tablet.climate.0001",
            "action": "set_room_target",
            "room_id": "living",
            "status": "pending",
            "updated_at": 1_785_949_200_000,
        }

        payload = climate_tablet_snapshot(
            None,
            climate_mode="disabled",
            active_operations=(operation,),
            generated_at=1_785_949_200_000,
        )

        self.assertEqual([operation], payload["active_operations"])
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_room_control_and_range_follow_authoritative_native_runtime(self) -> None:
        home = managed_home()
        control = home["rooms"][0]["control"]
        control["enabled"] = False
        control["allowed_actions"] = []
        control["blocked_reasons"] = ["device_unavailable"]
        target_input = control["action_inputs"]["set_room_target"][
            "target_temperature"
        ]
        target_input.update({"minimum": 19, "maximum": 27, "step": 1})

        payload = climate_tablet_snapshot(home, climate_mode="managed")
        room = payload["rooms"][0]

        self.assertFalse(room["control"]["enabled"])
        self.assertEqual([], room["control"]["allowed_actions"])
        self.assertNotIn("set_room_target", room["control"]["allowed_actions"])
        self.assertNotIn("set_room_target", room["control"].get("action_inputs", {}))
        self.assertEqual(
            ["device_unavailable"],
            room["control"]["blocked_reasons"],
        )
        self.assertEqual(19, room["minimum_temperature"])
        self.assertEqual(
            {"minimum": 19, "maximum": 27, "step": 1},
            room["temperature_range"],
        )
        contract_validator("climate-runtime.schema.json").validate(payload)

    def test_public_request_rejects_raw_home_assistant_target(self) -> None:
        request = action_request(managed_home()["state_revision"])
        request["entity_id"] = "climate.unsafe"

        with self.assertRaises(ClimateTabletViolation):
            parse_climate_tablet_action(request)

    def test_home_target_rejects_explicit_null_parameter(self) -> None:
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.home.1",
            "expected_state_revision": managed_home()["state_revision"],
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": None},
        }

        with self.assertRaises(ClimateTabletViolation):
            parse_climate_tablet_action(request)

    def test_recovery_preflight_revision_is_js_safe(self) -> None:
        from custom_components.hausman_hub.application.climate_tablet import (
            _canonical_fingerprint,
            _valid_recovery_preflight_record,
        )
        from custom_components.hausman_hub.climate_revision import MAX_JS_SAFE_INTEGER

        token = "recovery.v2." + "a" * 32
        item = {
            "token": token,
            "expires_at": 1,
            "preflight": {
                "room_id": "living",
                "control_revision": MAX_JS_SAFE_INTEGER,
                "resolved_device_ids": ["living_ac"],
                "available_device_ids": [],
                "desired_snapshot": {
                    "living_ac": {
                        "target_temperature": 24.0,
                        "target_humidity": 45,
                        "mode": "automatic",
                        "source_observed_at": None,
                    }
                },
                "preflight_snapshot_fingerprint": "",
                "snapshot_token": token,
            },
        }
        scope = {
            "room_id": "living",
            "control_revision": MAX_JS_SAFE_INTEGER,
            "resolved_device_ids": ["living_ac"],
            "desired_snapshot": item["preflight"]["desired_snapshot"],
        }
        item["preflight"]["preflight_snapshot_fingerprint"] = _canonical_fingerprint(scope)
        self.assertTrue(_valid_recovery_preflight_record(item))
        item["preflight"]["control_revision"] = MAX_JS_SAFE_INTEGER + 1
        self.assertFalse(_valid_recovery_preflight_record(item))


class ClimateTabletServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = 1_785_949_320_000
        self.home = managed_home()
        # Recovery is defined only for an explicitly user-excluded device.
        # Ordinary automatic and unknown devices must never be selected.
        device = self.home["rooms"][0]["devices"][0]
        device["mode"] = "manual"
        device["manual_reason"] = "user_excluded"
        device["observed_at"] = self.now - 1
        device["control"] = {
            "enabled": True,
            "allowed_actions": ["set_device_mode"],
            "blocked_reasons": [],
        }
        self.runtime = FakeRuntime(self.home)
        self.store = MemoryOperationStore()
        self.service = ClimateTabletService(
            self.runtime,
            self.store,
            operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
            now_ms=lambda: self.now,
            local_now=lambda: datetime(2026, 8, 5, tzinfo=timezone.utc),
        )

    async def test_reliability_readiness_is_off_until_external_ledger_loads(self) -> None:
        self.assertFalse(self.service.reliability_ready)
        await self.service.async_load()
        # The ordinary in-memory runtime and an initialized service are not
        # a substitute for the external authenticated ledger.
        self.assertFalse(self.service.reliability_ready)

    async def test_reliability_readiness_requires_verified_persistent_keyring(self) -> None:
        ready = ClimateTabletService(self.runtime, AuthenticatedLedgerMemoryStore())
        await ready.async_load()
        self.assertTrue(ready.reliability_ready)

        missing_keyring = ClimateTabletService(self.runtime, MemoryOperationStore())
        await missing_keyring.async_load()
        self.assertFalse(missing_keyring.reliability_ready)

    async def test_reliability_readiness_closes_after_persistence_failure(self) -> None:
        service = ClimateTabletService(
            self.runtime, FailingAuthenticatedLedgerMemoryStore(),
        )
        await service.async_load()
        self.assertTrue(service.reliability_ready)
        safe_snapshot = await service.async_snapshot()
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        with self.assertRaisesRegex(RuntimeError, "authenticated ledger save failure"):
            await service.async_execute(request)
        self.assertFalse(service.reliability_ready)
        # Read-only projection remains available, while every reliable
        # recovery surface closes before it can mutate expiry or read-back.
        self.assertEqual(safe_snapshot, await service.async_snapshot())
        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_recovery_v2_preflight("living")
        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_recovery_operation("0123456789abcdef0123456789abcdef")

    async def test_reservation_backend_failure_is_unavailable_not_a_cas_conflict(self) -> None:
        service = ClimateTabletService(self.runtime, FailingReservationStore())
        await service.async_load()
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        with self.assertRaisesRegex(ClimateTabletUnavailable, "reservation is unavailable"):
            await service.async_execute(request)
        self.assertFalse(service.reliability_ready)
        self.assertEqual([], self.runtime.commands)

    async def test_enhanced_action_accepts_fresh_control_revision_after_telemetry_changes(self) -> None:
        snapshot = await self.service.async_snapshot()
        request = action_request(snapshot["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=snapshot["control_revision"],
        )
        # Reported telemetry is not the enhanced action CAS token.
        self.runtime.home["state_revision"] += 1

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"], receipt)
        self.assertEqual(1, len(self.runtime.commands))

    async def test_enhanced_action_rejects_stale_control_revision_before_dispatch(self) -> None:
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        self.service._control_revision = 1

        with self.assertRaisesRegex(ClimateTabletViolation, "control revision changed"):
            await self.service.async_execute(request)
        self.assertEqual([], self.runtime.commands)

    async def test_legacy_action_still_rejects_stale_state_before_dispatch(self) -> None:
        request = action_request(self.home["state_revision"])
        self.runtime.home["state_revision"] += 1

        with self.assertRaisesRegex(ClimateTabletViolation, "state revision changed"):
            await self.service.async_execute(request)
        self.assertEqual([], self.runtime.commands)

    async def _recovery_v2_request(
        self,
        *,
        request_id: str,
        service: ClimateTabletService | None = None,
        device_ids: list[str] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Build a physical recovery request only from a server preflight."""
        from custom_components.hausman_hub.application.climate_tablet import (
            _recovery_request_fingerprint,
        )

        target = service or self.service
        preflight = await target.async_recovery_v2_preflight("living")
        request: dict[str, object] = {
            "contract": {
                "name": "hausman-hub-climate-room-recovery-request-v2",
                "version": 2,
            },
            "reliability_profile": "climate_recovery_proof_v1",
            "request_id": request_id,
            "expected_control_revision": preflight["control_revision"],
            "expected_desired_snapshot_fingerprint": preflight[
                "desired_snapshot_fingerprint"
            ],
            "expected_resolved_device_ids": preflight["resolved_device_ids"],
            "snapshot_token": preflight["snapshot_token"],
        }
        if device_ids is not None:
            request["device_ids"] = device_ids
        request["request_fingerprint"] = _recovery_request_fingerprint(request)
        return preflight, request

    async def test_reserves_before_execution_and_deduplicates_retry(self) -> None:
        request = action_request(self.home["state_revision"])

        receipt = await self.service.async_execute(request)
        duplicate = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertTrue(receipt["confirmed"])
        self.assertFalse(receipt["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(1, len(self.runtime.commands))
        self.assertEqual("pending", self.store.saved[0]["records"][0]["receipt"]["status"])
        self.assertEqual("confirmed", self.store.saved[1]["records"][0]["receipt"]["status"])
        snapshot = await self.service.async_snapshot()
        self.assertEqual(
            receipt["operation_id"],
            snapshot["rooms"][0]["devices"][0]["last_confirmed_operation"][
                "operation_id"
            ],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliability_receipt_is_scoped_and_pollable_without_redispatch(self) -> None:
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
            correlation_id="corr.tablet.climate.0001",
        )

        receipt = await self.service.async_execute(request)
        polled = await self.service.async_operation(receipt["operation_id"])
        duplicate = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(1, receipt["resulting_control_revision"])
        self.assertEqual(["living"], receipt["action_snapshot"]["resolved_scope"]["room_ids"])
        self.assertEqual(0, receipt["unfinished_device_count"])
        self.assertEqual(receipt["operation_id"], polled["operation_id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(1, len(self.runtime.commands))
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliability_request_id_rejects_changed_correlation(self) -> None:
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
            correlation_id="corr.tablet.climate.one",
        )
        await self.service.async_execute(request)
        request["correlation_id"] = "corr.tablet.climate.two"

        with self.assertRaisesRegex(ClimateTabletViolation, "already used"):
            await self.service.async_execute(request)

    async def test_partial_reliability_receipt_is_reobserved_without_redispatch(self) -> None:
        self.runtime.result_status = ContourApplyStatus.PARTIAL
        request = action_request(self.home["state_revision"])
        request.update(reliability_profile="climate_reliability_v1", expected_control_revision=0)
        receipt = await self.service.async_execute(request)
        self.assertEqual("partial", receipt["status"])
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertEqual((1, 1), (leaf["command_count"], leaf["accepted_count"]))

        self.service._operation_id_factory = lambda: "fedcba9876543210fedcba9876543210"
        successor = action_request(self.home["state_revision"], request_id="tablet.climate.physical.successor", target=24.0)
        successor.update(reliability_profile="climate_reliability_v1", expected_control_revision=1)
        await self.service.async_execute(successor)
        original = await self.service.async_operation(receipt["operation_id"])
        self.assertNotEqual("superseded_by_newer_intent", original["intent"]["status"])

        # The physical call was already made.  A later poll may only observe
        # the recovered state and must never emit a second command.
        self.runtime.home["rooms"][0]["target_temperature"] = request["parameters"]["target_temperature"]
        self.runtime.home["rooms"][0]["temporary_override"] = {
            "active": True, "target_temperature": request["parameters"]["target_temperature"],
        }
        refreshed = await self.service.async_operation(receipt["operation_id"])
        self.assertIn(refreshed["status"], {"partial", "confirmed"})
        self.assertEqual(2, len(self.runtime.commands))

    async def test_reliability_pending_has_honest_unfinished_leaves(self) -> None:
        self.runtime.result_status = ContourApplyStatus.PENDING
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertGreater(receipt["unfinished_device_count"], 0)
        device = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("accepted_unverified", device["execution_state"])
        self.assertEqual((1, 1), (device["command_count"], device["accepted_count"]))
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_synchronize_home_dispatches_bounded_explicit_action(self) -> None:
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.sync.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "synchronize_home",
            "room_id": None,
            "parameters": {},
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual([{"action": "synchronize_home"}], self.runtime.commands)
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

        unsafe = copy.deepcopy(request)
        unsafe["request_id"] = "tablet.climate.sync.2"
        unsafe["parameters"] = {"force": True}
        with self.assertRaises(ClimateTabletViolation):
            await self.service.async_execute(unsafe)

    async def test_home_targets_dispatch_once_for_temperature_and_humidity_shapes(self) -> None:
        for suffix, parameters in (
            ("temperature", {"target_temperature": 24.5}),
            ("both", {"target_temperature": 24.5, "target_humidity": 50}),
        ):
            with self.subTest(shape=suffix):
                runtime = FakeRuntime(copy.deepcopy(self.home))
                service = ClimateTabletService(
                    runtime, MemoryOperationStore(),
                    operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
                    now_ms=lambda: self.now,
                )
                request = {
                    "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                    "request_id": f"tablet.climate.home.{suffix}",
                    "correlation_id": f"corr.home.{suffix}",
                    "expected_state_revision": runtime.home["state_revision"],
                    "action": "set_home_targets",
                    "room_id": None,
                    "parameters": parameters,
                }

                receipt = await service.async_execute(request)

                self.assertEqual("confirmed", receipt["status"])
                self.assertTrue(receipt["confirmed"])
                self.assertEqual(1, len(runtime.commands))
                self.assertEqual("climate", runtime.commands[0]["contour_id"])
                self.assertEqual(f"corr.home.{suffix}", runtime.commands[0]["correlation_id"])
                self.assertTrue(runtime.commands[0]["confirm"])

    async def test_reserved_home_target_loses_to_direct_writer_without_save_or_dispatch(self) -> None:
        """A stale reserved tablet handoff never crosses the native boundary."""

        class PausingHomeTargetRuntime(ClimateRuntime):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)
                self.home_target_entered = asyncio.Event()
                self.resume_home_target = asyncio.Event()

            async def async_home_climate_targets(self, payload: object, **kwargs: object):
                self.home_target_entered.set()
                await self.resume_home_target.wait()
                return await super().async_home_climate_targets(payload, **kwargs)

        store = SharedAuthenticatedLedgerMemoryStore()
        registry, contours = build_climate_contour_setup(
            MemoryBridge().snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry, state_view = native_application_inputs(registry)
        executor = ReflectingStrictExecutor(state_view)
        contour_store = MemoryContourStore(contours)
        runtime = PausingHomeTargetRuntime(
            entry_id="entry",
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            strict_ha_call_executor=executor,
            ha_state_view=state_view,
            operation_id_factory=iter(("a" * 32, "b" * 32)).__next__,
            now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await runtime.async_start()
        service = ClimateTabletService(
            runtime,
            store,
            operation_id_factory=lambda: "c" * 32,
            now_ms=lambda: 1784280005000,
        )
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.cross-writer",
            "correlation_id": "corr.cross-writer",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5},
        }

        first_task = asyncio.create_task(service.async_execute(request))
        await asyncio.wait_for(runtime.home_target_entered.wait(), timeout=1)
        second = await runtime.async_temporary_temperature(
            {
                "request_id": "direct.climate.cross-writer",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 24.5,
                "confirm": True,
                "reliability_profile": "climate_reliability_v1",
                "expected_control_revision": 1,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        runtime.resume_home_target.set()
        first = await asyncio.wait_for(first_task, timeout=1)

        saved_room = contour_store.registry.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual("confirmed", second.status.value)
        self.assertEqual("unavailable", first["status"])
        self.assertEqual("action_unsupported", first["reason"])
        self.assertEqual(24.5, saved_room.target_temperature)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(1, len(executor.batches))

    async def test_reserved_home_target_rechecks_revision_after_preflight_observation(self) -> None:
        """A reservation lost while read-back waits cannot create native state."""

        class PausingObservationRuntime(ClimateRuntime):
            def __init__(self, **kwargs: object) -> None:
                super().__init__(**kwargs)
                self.observation_entered = asyncio.Event()
                self.resume_observation = asyncio.Event()
                self._pause_next_observation = False

            async def _async_native_climate_observation_unlocked(self, **kwargs: object):
                if self._pause_next_observation and self._control_revision == 1:
                    self._pause_next_observation = False
                    self.observation_entered.set()
                    await self.resume_observation.wait()
                return await super()._async_native_climate_observation_unlocked(**kwargs)

        store = SharedAuthenticatedLedgerMemoryStore()
        registry, contours = build_climate_contour_setup(
            MemoryBridge().snapshot, room_ids=["living"],
            source_ids=["synthetic-ac-source-living"], name="Климат",
            mode="automatic", target_temperature=25.0, target_humidity=45,
            strategy="normal",
        )
        registry, state_view = native_application_inputs(registry)
        executor = ReflectingStrictExecutor(state_view)
        contour_store = MemoryContourStore(contours)
        runtime = PausingObservationRuntime(
            entry_id="entry", configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=MemoryStore(registry), contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=state_view,
            operation_id_factory=lambda: "a" * 32,
            now_ms=lambda: 1784280005000, direct_control_store=store,
        )
        await runtime.async_start()
        runtime._pause_next_observation = True
        service = ClimateTabletService(runtime, store, operation_id_factory=lambda: "b" * 32,
                                       now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.observation-race", "correlation_id": "corr.observation-race",
            "expected_state_revision": 0, "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1", "action": "set_home_targets",
            "room_id": None, "parameters": {"target_temperature": 25.5},
        }
        task = asyncio.create_task(service.async_execute(request))
        await asyncio.wait_for(runtime.observation_entered.wait(), timeout=1)
        self.assertEqual(2, await store.async_reserve_control_revision(1))
        runtime.resume_observation.set()
        receipt = await asyncio.wait_for(task, timeout=1)

        self.assertEqual("unavailable", receipt["status"])
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)

    async def test_reserved_home_target_runs_once_and_duplicate_reuses_receipt(self) -> None:
        """Without an interleaving, the reserved handoff has one dispatch."""

        store = SharedAuthenticatedLedgerMemoryStore()
        registry, contours = build_climate_contour_setup(
            MemoryBridge().snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry, state_view = native_application_inputs(registry)
        executor = ReflectingStrictExecutor(state_view)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            strict_ha_call_executor=executor,
            ha_state_view=state_view,
            operation_id_factory=lambda: "d" * 32,
            now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await runtime.async_start()
        service = ClimateTabletService(
            runtime,
            store,
            operation_id_factory=lambda: "e" * 32,
            now_ms=lambda: 1784280005000,
        )
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.uncontended",
            "correlation_id": "corr.uncontended",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5},
        }

        first = await service.async_execute(request)
        duplicate = await service.async_execute(request)

        self.assertNotEqual("unavailable", first["status"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        self.assertEqual(1, len(executor.batches))

    async def test_reserved_home_humidity_target_dispatches_only_one_humidifier_batch(self) -> None:
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=True,
        )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.home-humidity",
            "correlation_id": "corr.home-humidity",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_humidity": 55},
        }

        receipt = await service.async_execute(request)
        duplicate = await service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"], receipt)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt["operation_id"], duplicate["operation_id"])
        self.assertEqual(1, len(executor.batches))
        calls = executor.batches[0]
        self.assertEqual(1, len(calls))
        self.assertEqual(ClimateHaService.HUMIDIFIER_SET_HUMIDITY, calls[0].service)
        self.assertEqual("humidifier.living", calls[0].entity_id)
        self.assertEqual(55, calls[0].humidity)
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(1, store._control_revision)
        self.assertEqual(
            "climate_reliability_v1",
            store.payload["records"][0]["request"]["reliability_profile"],
        )

    async def test_reserved_home_targets_dispatch_complete_temperature_and_humidity_scope_once(self) -> None:
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=True,
        )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        receipt = await service.async_execute({
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.home-combined",
            "correlation_id": "corr.home-combined",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5, "target_humidity": 55},
        })

        self.assertEqual("confirmed", receipt["status"], receipt)
        # Execution is checkpointed per physical owner, so the complete
        # four-device scope is four one-call batches, never one ambiguous
        # aggregate batch or a duplicate retry.
        self.assertEqual(4, len(executor.batches))
        calls = tuple(call for batch in executor.batches for call in batch)
        self.assertEqual(
            {
                (ClimateHaService.CLIMATE_SET_TEMPERATURE, "climate.living_air_conditioner", 25.5),
                (ClimateHaService.CLIMATE_SET_TEMPERATURE, "climate.living_radiator", 25.5),
                (ClimateHaService.CLIMATE_SET_TEMPERATURE, "climate.living_floor", 25.5),
                (ClimateHaService.HUMIDIFIER_SET_HUMIDITY, "humidifier.living", 55),
            },
            {(call.service, call.entity_id, call.temperature if call.temperature is not None else call.humidity) for call in calls},
        )
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual(1, store._control_revision)

    async def test_reserved_home_temperature_mixed_alignment_confirms_exact_leaf_map_once(self) -> None:
        """Already-aligned owners remain terminal beside one dispatched owner."""
        runtime, store, _contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        # AC and floor already report the requested target. Only the radiator
        # needs one strict call, while all three owners remain in the receipt.
        for entity_id in (
            "climate.living_air_conditioner",
            "climate.living_floor",
        ):
            current = executor._state_view.states[entity_id]
            executor._state_view.states[entity_id] = replace(
                current, attributes={**current.attributes, "temperature": 25.5},
            )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.home-mixed-temperature",
            "correlation_id": "corr.home-mixed-temperature",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5},
        }

        receipt = await service.async_execute(request)
        duplicate = await service.async_execute(request)
        restarted = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await restarted.async_load()
        after_restart = await restarted.async_execute(request)

        self.assertEqual("confirmed", receipt["status"], receipt)
        self.assertTrue(duplicate["duplicate"])
        self.assertTrue(after_restart["duplicate"])
        self.assertEqual(receipt["operation_id"], after_restart["operation_id"])
        self.assertEqual(1, len(executor.batches))
        calls = executor.batches[0]
        self.assertEqual(1, len(calls))
        self.assertEqual("climate.living_radiator", calls[0].entity_id)
        leaves = receipt["outcomes"]["rooms"]["living"]["devices"]
        self.assertEqual(
            {"living_air_conditioner", "living_radiator", "living_floor"},
            set(leaves),
        )
        self.assertEqual("already_in_sync", leaves["living_air_conditioner"]["execution_state"])
        self.assertEqual("applied", leaves["living_radiator"]["execution_state"])
        self.assertEqual(1, receipt["read_back"]["evidence"]["accepted_count"])

    async def test_reserved_home_target_defers_all_offline_owners(self) -> None:
        runtime, store, contour_store, executor = native_home_target_runtime(include_humidifier=False)
        for entity_id in ("climate.living_air_conditioner", "climate.living_radiator", "climate.living_floor"):
            current = executor._state_view.states[entity_id]
            executor._state_view.states[entity_id] = replace(current, state="unavailable", attributes={})
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {"contract": {"name": "hausman-hub-climate-action-request", "version": 1}, "request_id": "tablet.climate.home-offline", "correlation_id": "corr.home-offline", "expected_state_revision": 0, "expected_control_revision": 0, "reliability_profile": "climate_reliability_v1", "action": "set_home_targets", "room_id": None, "parameters": {"target_temperature": 25.5}}
        receipt = await service.async_execute(request)
        self.assertEqual("partial", receipt["status"], receipt)
        self.assertTrue(receipt["final"])
        self.assertEqual("saved_deferred_offline", receipt["intent"]["status"])
        self.assertEqual(0, len(executor.batches))
        self.assertEqual(1, len(contour_store.saved))
        self.assertTrue((await service.async_execute(request))["duplicate"])
        restarted = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await restarted.async_load()
        replay = await restarted.async_execute(request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(receipt["operation_id"], replay["operation_id"])
        self.assertEqual(0, len(executor.batches))

    async def test_reserved_home_target_rejects_unknown_or_incomplete_available_owner_before_persistence(self) -> None:
        """Unknown HA state must never be treated as safely deferrable offline state."""
        cases = (
            ("unknown", None),
            ("unexpected_state", None),
            ("cool", {"hvac_action": "cooling"}),
        )
        for index, (state, attributes) in enumerate(cases):
            with self.subTest(state=state, attributes=attributes):
                runtime, store, contour_store, executor = native_home_target_runtime(
                    include_humidifier=False,
                )
                current = runtime._ha_state_view.states["climate.living_air_conditioner"]
                runtime._ha_state_view.states["climate.living_air_conditioner"] = replace(
                    current, state=state,
                    attributes=dict(current.attributes) if attributes is None else attributes,
                )
                await runtime.async_start()
                service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
                await service.async_load()
                request = {
                    "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                    "request_id": f"tablet.climate.unknown-{index}",
                    "correlation_id": f"corr.unknown-{index}",
                    "expected_state_revision": 0, "expected_control_revision": 0,
                    "reliability_profile": "climate_reliability_v1", "action": "set_home_targets",
                    "room_id": None, "parameters": {"target_temperature": 25.5},
                }

                with self.assertRaises(ClimateTabletUnavailable):
                    await service.async_execute(request)

                self.assertIsNone(store.payload)
                self.assertEqual([], store.saved)
                self.assertEqual([], contour_store.saved)
                self.assertEqual([], executor.batches)

    async def test_reserved_home_targets_retain_mixed_offline_scope_across_restart(self) -> None:
        """An offline owner is saved as deferred and never replayed by a duplicate."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        runtime = ClimateRuntime(
            entry_id="entry", configuration=runtime.configuration,
            registry_store=runtime._registry_store, contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            operation_id_factory=iter(f"{index:032x}" for index in range(1, 20)).__next__,
            now_ms=lambda: 1784280005000, direct_control_store=store,
        )
        # This is a production-shaped multi-owner contour: only the floor is
        # currently available, while the other statically valid owners are
        # offline.  The target remains a single whole-home intent.
        for entity_id in (
            "climate.living_air_conditioner",
            "climate.living_radiator",
        ):
            current = executor._state_view.states[entity_id]
            executor._state_view.states[entity_id] = replace(
                current, state="unavailable", attributes={},
            )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request_a = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.mixed-offline-a",
            "correlation_id": "corr.mixed-offline-a",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5},
        }
        receipt_a = await service.async_execute(request_a)
        duplicate_a = await service.async_execute(request_a)
        self.assertEqual("partial", receipt_a["status"], receipt_a)
        self.assertTrue(receipt_a["final"], receipt_a)
        self.assertEqual(["set_home_targets", "synchronize_home"], (
            await service.async_snapshot()
        )["home_control"]["allowed_actions"])
        request_b = {
            **request_a,
            "request_id": "tablet.climate.mixed-offline-b",
            "correlation_id": "corr.mixed-offline-b",
            "expected_control_revision": 1,
            "parameters": {"target_temperature": 25.0},
        }
        receipt_b = await service.async_execute(request_b)
        duplicate_b = await service.async_execute(request_b)

        self.assertEqual((1, 2), (
            receipt_a["resulting_control_revision"],
            receipt_b["resulting_control_revision"],
        ))
        self.assertIsNone(runtime.last_error, runtime.last_error)
        self.assertEqual([25.5, 25.0], [
            saved.contour("climate").rooms[0].target_temperature
            for saved in contour_store.saved
        ], receipt_b)
        self.assertTrue(duplicate_a["duplicate"])
        self.assertTrue(duplicate_b["duplicate"])
        self.assertEqual(receipt_a["operation_id"], duplicate_a["operation_id"])
        self.assertEqual(receipt_b["operation_id"], duplicate_b["operation_id"])
        calls = tuple(call for batch in executor.batches for call in batch)
        self.assertEqual([
            ("climate.living_floor", 25.5),
            ("climate.living_floor", 25.0),
        ], [(call.entity_id, call.temperature) for call in calls])
        for receipt in (receipt_a, receipt_b):
            self.assertEqual("partial", receipt["status"], receipt)
            self.assertTrue(receipt["final"])
            self.assertEqual("saved_deferred_offline", receipt["intent"]["status"])
            leaves = receipt["outcomes"]["rooms"]["living"]["devices"]
            for device_id in ("living_air_conditioner", "living_radiator"):
                self.assertEqual("deferred", leaves[device_id]["status"])
                self.assertEqual("device_unavailable", leaves[device_id]["reason"])
                self.assertEqual((0, 0), (
                    leaves[device_id]["command_count"], leaves[device_id]["accepted_count"],
                ))
                self.assertNotIn("execution_state", leaves[device_id])
            self.assertIn(leaves["living_floor"].get("execution_state"), {
                "applied", "already_in_sync",
            })

        # Reconnecting an old owner after restart cannot turn a retained
        # duplicate into a new physical command.
        for entity_id in (
            "climate.living_air_conditioner",
            "climate.living_radiator",
        ):
            current = executor._state_view.states[entity_id]
            executor._state_view.states[entity_id] = replace(
                current, state="cool", attributes={"temperature": 25.0},
            )
        restarted_runtime = ClimateRuntime(
            entry_id="entry", configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=runtime._registry_store, contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "b" * 32,
            now_ms=lambda: 1784280005000, direct_control_store=store,
        )
        await restarted_runtime.async_start()
        restarted = ClimateTabletService(restarted_runtime, store, now_ms=lambda: 1784280005000)
        await restarted.async_load()
        replay_b = await restarted.async_execute(request_b)
        self.assertTrue(replay_b["duplicate"])
        self.assertEqual(receipt_b["operation_id"], replay_b["operation_id"])
        self.assertEqual(2, len(executor.batches))

        request_c = {**request_b, "request_id": "tablet.climate.mixed-offline-c",
                     "correlation_id": "corr.mixed-offline-c",
                     "expected_control_revision": 2,
                     "parameters": {"target_temperature": 25.5}}
        await restarted.async_execute(request_c)
        self.assertGreater(len(executor.batches), 2)

    async def test_reserved_home_target_skips_real_manual_owner_but_rejects_missing_owner(self) -> None:
        """Manual memory excludes a valid owner, never hides a broken binding."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        # Use the production ownership API so the test exercises durable
        # manual attribution rather than modifying private memory.
        await runtime.async_set_device_mode("living", "living_radiator", "manual")
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.manual-owner",
            "correlation_id": "corr.manual-owner",
            "expected_state_revision": 0, "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1", "action": "set_home_targets",
            "room_id": None, "parameters": {"target_temperature": 25.5},
        }
        receipt = await service.async_execute(request)
        leaves = receipt["outcomes"]["rooms"]["living"]["devices"]
        manual = leaves["living_radiator"]
        self.assertEqual("partial", receipt["status"])
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["final"])
        self.assertEqual("saved_for_manual_device", receipt["intent"]["status"])
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual("manual", manual["status"])
        self.assertEqual("user_excluded", manual["reason"])
        self.assertEqual("manual_excluded", manual["message_code"])
        self.assertTrue(manual["message"])
        self.assertEqual((0, 0), (manual["command_count"], manual["accepted_count"]))
        self.assertNotIn("execution_state", manual)
        automatic = leaves["living_air_conditioner"]
        self.assertIn(automatic.get("execution_state"), {"applied", "already_in_sync"})
        self.assertEqual(
            (1, 1) if automatic["execution_state"] == "applied" else (0, 0),
            (automatic["command_count"], automatic["accepted_count"]),
        )
        self.assertNotIn("climate.living_radiator", [
            call.entity_id for batch in executor.batches for call in batch
        ])

        restarted_runtime = ClimateRuntime(
            entry_id="entry", configuration=runtime.configuration,
            registry_store=runtime._registry_store, contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "c" * 32, now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await restarted_runtime.async_start()
        restarted = ClimateTabletService(
            restarted_runtime, store, now_ms=lambda: 1784280005000,
        )
        await restarted.async_load()
        batches_before_duplicate = len(executor.batches)
        duplicate = await restarted.async_execute(request)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt["operation_id"], duplicate["operation_id"])
        self.assertEqual(receipt, {**duplicate, "duplicate": False})
        self.assertEqual(batches_before_duplicate, len(executor.batches))

        missing = next(device for device in runtime._registry.devices if device.device_id == "living_radiator")
        missing_registry = replace(
            runtime._registry,
            devices=tuple(
                replace(device, endpoints=()) if device.device_id == missing.device_id else device
                for device in runtime._registry.devices
            ),
        )
        runtime._registry_store.registry = missing_registry
        runtime._registry = missing_registry
        before_saves = len(contour_store.saved)
        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_execute({
                **request, "request_id": "tablet.climate.manual-missing-owner",
                "correlation_id": "corr.manual-missing-owner",
                "expected_control_revision": 1,
                "parameters": {"target_temperature": 25.0},
            })
        self.assertEqual(before_saves, len(contour_store.saved))

    async def test_reserved_home_target_preserves_real_external_off_owner_across_restart(self) -> None:
        """An external shutdown remains distinct from a user exclusion in a frozen receipt."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        await runtime.async_set_device_mode("living", "living_radiator", "manual")
        attribution = next(
            item
            for item in runtime._manual_memory.attributions
            if item.device_id == "living_radiator"
        )
        runtime._manual_memory = replace(
            runtime._manual_memory,
            attributions=(replace(attribution, reason="external_off"),),
        )
        await runtime._async_save_manual(runtime._manual_memory)
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.external-off-owner",
            "correlation_id": "corr.external-off-owner",
            "expected_state_revision": 0, "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1", "action": "set_home_targets",
            "room_id": None, "parameters": {"target_temperature": 25.5},
        }

        receipt = await service.async_execute(request)
        leaves = receipt["outcomes"]["rooms"]["living"]["devices"]
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)
        external_off = leaves["living_radiator"]
        self.assertEqual("partial", receipt["status"])
        self.assertTrue(receipt["accepted"])
        self.assertTrue(receipt["final"])
        self.assertEqual("saved_for_manual_device", receipt["intent"]["status"])
        self.assertEqual("manual", external_off["status"])
        self.assertEqual("external_off", external_off["reason"])
        self.assertEqual("external_off", external_off["message_code"])
        self.assertEqual("Устройство выключено вручную и исключено из контура.", external_off["message"])
        self.assertEqual((0, 0), (external_off["command_count"], external_off["accepted_count"]))
        self.assertNotIn("execution_state", external_off)
        automatic = leaves["living_air_conditioner"]
        self.assertIn(automatic.get("execution_state"), {"applied", "already_in_sync"})
        self.assertEqual(
            (1, 1) if automatic["execution_state"] == "applied" else (0, 0),
            (automatic["command_count"], automatic["accepted_count"]),
        )
        self.assertNotIn("climate.living_radiator", [
            call.entity_id for batch in executor.batches for call in batch
        ])

        restarted_runtime = ClimateRuntime(
            entry_id="entry", configuration=runtime.configuration,
            registry_store=runtime._registry_store, contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "d" * 32, now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await restarted_runtime.async_start()
        restarted = ClimateTabletService(
            restarted_runtime, store, now_ms=lambda: 1784280005000,
        )
        await restarted.async_load()
        batches_before_duplicate = len(executor.batches)
        duplicate = await restarted.async_execute(request)
        contract_validator("climate-operation-receipt.schema.json").validate(duplicate)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt["operation_id"], duplicate["operation_id"])
        self.assertEqual(receipt, {**duplicate, "duplicate": False})
        self.assertEqual(batches_before_duplicate, len(executor.batches))

    async def test_reserved_home_target_marks_every_manual_room_leaf_terminal_without_dispatch(self) -> None:
        """A room-level manual choice owns every selected actuator, even offline ones."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        offline = runtime._ha_state_view.states["climate.living_floor"]
        runtime._ha_state_view.states["climate.living_floor"] = replace(
            offline, state="unavailable", attributes={},
        )
        await runtime.async_set_room_mode("living", "manual")
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.manual-room", "correlation_id": "corr.manual-room",
            "expected_state_revision": 0, "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1", "action": "set_home_targets",
            "room_id": None, "parameters": {"target_temperature": 25.5},
        }

        receipt = await service.async_execute(request)

        self.assertEqual("partial", receipt["status"], receipt)
        self.assertTrue(receipt["final"])
        self.assertEqual("saved_for_manual_device", receipt["intent"]["status"])
        self.assertEqual(1, len(contour_store.saved))
        self.assertEqual([], executor.batches)
        leaves = receipt["outcomes"]["rooms"]["living"]["devices"]
        self.assertEqual(
            {"living_air_conditioner", "living_radiator", "living_floor"}, set(leaves),
        )
        for leaf in leaves.values():
            self.assertEqual("manual", leaf["status"])
            self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
            self.assertNotIn("execution_state", leaf)
        restarted = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await restarted.async_load()
        replay = await restarted.async_execute(request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual([], executor.batches)

    async def test_reserved_home_target_rejects_plan_changed_after_tablet_preflight(self) -> None:
        """The reserved native fingerprint closes the preflight-to-dispatch race."""
        class PlanChangingRuntime(ClimateRuntime):
            async def async_preflight_home_climate_targets(self, payload: object) -> dict[str, object]:
                result = await super().async_preflight_home_climate_targets(payload)
                state = self._ha_state_view.states["climate.living_floor"]
                self._ha_state_view.states["climate.living_floor"] = replace(
                    state, state="unavailable", attributes={},
                )
                return result

        runtime, store, contour_store, executor = native_home_target_runtime(include_humidifier=False)
        runtime = PlanChangingRuntime(
            entry_id="entry", configuration=runtime.configuration,
            registry_store=runtime._registry_store, contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "a" * 32, now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        original = contour_store.registry
        receipt = await service.async_execute({
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.plan-race", "correlation_id": "corr.plan-race",
            "expected_state_revision": 0, "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1", "action": "set_home_targets",
            "room_id": None, "parameters": {"target_temperature": 25.5},
        })

        self.assertEqual("unavailable", receipt["status"], receipt)
        self.assertTrue(receipt["final"])
        self.assertEqual(original, contour_store.registry)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)

    async def test_native_snapshot_advertises_home_target_for_offline_but_not_missing_scope(self) -> None:
        """An offline owner remains eligible, unlike a missing required binding."""
        runtime, store, _contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        state = executor._state_view.states["climate.living_radiator"]
        executor._state_view.states["climate.living_radiator"] = replace(
            state, state="unavailable", attributes={},
        )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        self.assertIn("set_home_targets", (
            await service.async_snapshot()
        )["home_control"]["allowed_actions"])

        runtime, store, _contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        missing_registry = replace(
            runtime._registry,
            devices=tuple(
                replace(device, endpoints=())
                if device.device_id == "living_radiator" else device
                for device in runtime._registry.devices
            ),
        )
        runtime._registry_store.registry = missing_registry
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        self.assertNotIn("set_home_targets", (
            await service.async_snapshot()
        )["home_control"]["allowed_actions"])

    async def test_reserved_home_combined_mixed_axis_alignment_confirms_once(self) -> None:
        """The mixed proof works in both temperature and humidity directions."""
        for aligned_temperature, suffix in ((True, "temperature"), (False, "humidity")):
            with self.subTest(aligned_temperature=aligned_temperature):
                runtime, store, _contour_store, executor = native_home_target_runtime(
                    include_humidifier=True,
                )
                if aligned_temperature:
                    for entity_id in (
                        "climate.living_air_conditioner",
                        "climate.living_radiator",
                        "climate.living_floor",
                    ):
                        current = executor._state_view.states[entity_id]
                        executor._state_view.states[entity_id] = replace(
                            current, attributes={**current.attributes, "temperature": 25.5},
                        )
                else:
                    current = executor._state_view.states["humidifier.living"]
                    executor._state_view.states["humidifier.living"] = replace(
                        current, attributes={**current.attributes, "humidity": 55},
                    )
                await runtime.async_start()
                service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
                await service.async_load()
                request = {
                    "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                    "request_id": f"tablet.climate.home-mixed-{suffix}",
                    "correlation_id": f"corr.home-mixed-{suffix}",
                    "expected_state_revision": 0,
                    "expected_control_revision": 0,
                    "reliability_profile": "climate_reliability_v1",
                    "action": "set_home_targets", "room_id": None,
                    "parameters": {"target_temperature": 25.5, "target_humidity": 55},
                }
                receipt = await service.async_execute(request)
                duplicate = await service.async_execute(request)
                restarted = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
                await restarted.async_load()
                after_restart = await restarted.async_execute(request)

                self.assertEqual("confirmed", receipt["status"], receipt)
                self.assertTrue(duplicate["duplicate"])
                self.assertTrue(after_restart["duplicate"])
                self.assertEqual(receipt["operation_id"], after_restart["operation_id"])
                calls = tuple(call for batch in executor.batches for call in batch)
                self.assertEqual(1 if aligned_temperature else 3, len(calls))
                self.assertEqual(
                    1 if aligned_temperature else 3,
                    receipt["read_back"]["evidence"]["accepted_count"],
                )

    async def test_reserved_home_target_invalid_final_call_never_saves_or_dispatches(self) -> None:
        """Final owner validation happens before the mutable contour boundary."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        before = contour_store.registry
        runtime._calls_match_strict_registry = lambda *args, **kwargs: False
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        receipt = await service.async_execute({
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.invalid-frozen-call",
            "correlation_id": "corr.invalid-frozen-call",
            "expected_state_revision": 0, "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets", "room_id": None,
            "parameters": {"target_temperature": 25.5},
        })

        self.assertEqual("unavailable", receipt["status"])
        self.assertEqual(before, contour_store.registry)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)

    async def test_reserved_home_target_contour_save_failure_closes_native_checkpoint_without_dispatch(self) -> None:
        """A failed contour save closes the already durable native reservation."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        contour_before = contour_store.registry
        contour_store.fail = True
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.home-contour-save-failure",
            "correlation_id": "corr.home-contour-save-failure",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5},
        }

        receipt = await service.async_execute(request)
        duplicate = await service.async_execute(request)

        self.assertEqual("unavailable", receipt["status"], receipt)
        self.assertTrue(receipt["final"])
        self.assertTrue(
            climate_tablet_module._receipt_matches_request(
                receipt, parse_climate_tablet_action(request)
            ),
            receipt,
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt["operation_id"], duplicate["operation_id"])
        self.assertEqual(contour_before, contour_store.registry)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"]
        self.assertEqual("not_attempted", leaf["status"])
        self.assertEqual("device_unavailable", leaf["reason"])
        self.assertEqual("blocked_before_dispatch", leaf["execution_state"])
        native_record = store._direct_control_records[0]
        self.assertEqual("unavailable", native_record["receipt"]["status"])
        native_leaf = native_record["receipt"]["outcomes"]["rooms"]["living"]["devices"]["living_air_conditioner"]
        self.assertEqual(
            "blocked_before_dispatch",
            native_leaf["execution_state"],
        )
        self.assertEqual((0, 0), (native_leaf["command_count"], native_leaf["accepted_count"]))

        restarted_runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=runtime._registry_store,
            contour_store=contour_store,
            strict_ha_call_executor=executor,
            ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "b" * 32,
            now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await restarted_runtime.async_start()
        restarted = ClimateTabletService(
            restarted_runtime, store, now_ms=lambda: 1784280005000,
        )
        await restarted.async_load()
        after_restart = await restarted.async_execute(request)

        self.assertTrue(after_restart["duplicate"])
        self.assertEqual(receipt["operation_id"], after_restart["operation_id"])
        self.assertEqual([], executor.batches)

    async def test_reserved_home_target_terminal_native_checkpoint_failure_fails_closed(self) -> None:
        """A stale native pending record is never allowed to trigger a retry."""
        runtime, _store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        store = FailingDirectControlCheckpointStore(fail_on_direct_save=2)
        runtime._direct_control_store = store
        contour_before = contour_store.registry
        contour_store.fail = True
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.home-terminal-checkpoint-failure",
            "correlation_id": "corr.home-terminal-checkpoint-failure",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_temperature": 25.5},
        }

        receipt = await service.async_execute(request)

        self.assertEqual("unavailable", receipt["status"], receipt)
        self.assertFalse(service.reliability_ready)
        self.assertEqual(contour_before, contour_store.registry)
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)
        self.assertEqual("pending", store._direct_control_records[0]["receipt"]["status"])
        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_execute({**request, "request_id": "tablet.climate.home-blocked"})

        restarted_runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=runtime._registry_store,
            contour_store=contour_store,
            strict_ha_call_executor=executor,
            ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "c" * 32,
            now_ms=lambda: 1784280005000,
            direct_control_store=store,
        )
        await restarted_runtime.async_start()
        restarted = ClimateTabletService(
            restarted_runtime, store, now_ms=lambda: 1784280005000,
        )
        await restarted.async_load()
        duplicate = await restarted.async_execute(request)

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt["operation_id"], duplicate["operation_id"])
        self.assertEqual([], executor.batches)

    async def test_reserved_home_humidity_target_rejects_missing_actuator_before_durable_or_executor_work(self) -> None:
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()

        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_execute({
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.home-humidity-missing",
            "correlation_id": "corr.home-humidity-missing",
            "expected_state_revision": 0,
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_home_targets",
            "room_id": None,
            "parameters": {"target_humidity": 55},
            })

        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)
        self.assertEqual(0, store._control_revision)
        self.assertIsNone(store.payload)

    async def test_reserved_combined_home_target_rejects_missing_temperature_axis_before_durable_work(self) -> None:
        """Each requested axis needs its own complete managed actuator set."""
        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=True,
        )
        await runtime.async_start()
        # Leave a valid humidifier, but remove every temperature actuator.
        runtime._registry = ClimateRegistry(
            rooms=runtime._registry.rooms,
            devices=tuple(
                device for device in runtime._registry.devices
                if device.kind is ClimateDeviceKind.HUMIDIFIER
            ),
        )
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()

        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_execute({
                "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                "request_id": "tablet.climate.home-temperature-missing",
                "correlation_id": "corr.home-temperature-missing",
                "expected_state_revision": 0,
                "expected_control_revision": 0,
                "reliability_profile": "climate_reliability_v1",
                "action": "set_home_targets",
                "room_id": None,
                "parameters": {"target_temperature": 25.5, "target_humidity": 55},
            })

        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)
        self.assertEqual(0, store._control_revision)
        self.assertIsNone(store.payload)

    async def test_legacy_home_duplicate_returns_before_a_fresh_snapshot(self) -> None:
        """A legacy retry reuses its operation identity without re-entering readiness."""
        runtime = FakeRuntime(managed_home())
        # Reliable dispatch requires a real pre-dispatch observation proof.
        runtime._mark_observed()
        store = AuthenticatedLedgerMemoryStore()
        service = ClimateTabletService(runtime, store)
        await service.async_load()

        first = await service.async_execute_legacy_home_targets(
            request_id="tablet.climate.legacy-duplicate",
            correlation_id="corr.legacy-duplicate",
            parameters={"target_temperature": 24.5},
        )
        sidecar = await store.async_load_reliable_scope_bindings()
        self.assertEqual(
            first["operation_id"],
            sidecar["__legacy_home_execution_facts__"][
                "corr.legacy-duplicate"
            ]["operation_id"],
        )

        restarted = ClimateTabletService(runtime, store)
        await restarted.async_load()
        self.assertEqual(
            first["operation_id"],
            restarted._legacy_home_execution_facts[
                "corr.legacy-duplicate"
            ]["operation_id"],
        )

        async def no_new_snapshot() -> dict[str, object]:
            raise AssertionError("duplicate must not read a new climate revision")

        runtime.async_public_snapshot = no_new_snapshot
        duplicate = await restarted.async_execute_legacy_home_targets(
            request_id="tablet.climate.legacy-duplicate",
            correlation_id="corr.legacy-duplicate",
            parameters={"target_temperature": 24.5},
        )

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        self.assertEqual(1, len(runtime.commands))

    async def test_legacy_home_correlation_is_the_durable_duplicate_identity(self) -> None:
        """A transport request id cannot cause a second legacy home dispatch."""
        runtime = FakeRuntime(managed_home())
        runtime._mark_observed()
        store = AuthenticatedLedgerMemoryStore()
        service = ClimateTabletService(runtime, store)
        await service.async_load()
        correlation_id = "c" + "x" * 127

        first = await service.async_execute_legacy_home_targets(
            request_id="tablet.climate.legacy-correlation-a",
            correlation_id=correlation_id,
            parameters={"target_temperature": 24.5},
        )
        restarted = ClimateTabletService(runtime, store)
        await restarted.async_load()
        duplicate = await restarted.async_execute_legacy_home_targets(
            request_id="tablet.climate.legacy-correlation-b",
            correlation_id=correlation_id,
            parameters={"target_temperature": 24.5},
        )

        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        self.assertEqual(1, len(runtime.commands))
        with self.assertRaises(ClimateTabletViolation) as conflict:
            await restarted.async_execute_legacy_home_targets(
                request_id="tablet.climate.legacy-correlation-c",
                correlation_id=correlation_id,
                parameters={"target_temperature": 25.0},
            )
        self.assertEqual("revision_conflict", conflict.exception.code)

    async def test_home_target_preflight_denial_never_reserves_or_dispatches(self) -> None:
        for name, mutate in (
            ("storage", lambda home: home["rooms"][0]["control"].update(allowed_actions=[])),
            ("executor", lambda home: home["climate"].update(fresh=False)),
            ("native-preflight", lambda home: home["rooms"][0].update(mode="manual")),
        ):
            with self.subTest(name=name):
                home = copy.deepcopy(self.home)
                mutate(home)
                runtime = FakeRuntime(home)
                store = MemoryOperationStore()
                service = ClimateTabletService(runtime, store)
                request = {
                    "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                    "request_id": f"tablet.climate.denied.{name}",
                    "expected_state_revision": home["state_revision"],
                    "action": "set_home_targets", "room_id": None,
                    "parameters": {"target_temperature": 24.5},
                }
                with self.assertRaises(ClimateTabletViolation):
                    await service.async_execute(request)
                self.assertEqual([], store.saved)
        self.assertEqual([], runtime.commands)

    async def test_legacy_home_contour_save_failure_persists_zero_pre_dispatch_fact(self) -> None:
        """The compatibility route keeps a proven no-dispatch failure replayable."""

        runtime, store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        await runtime.async_start()
        contour_store.fail = True
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        kwargs = {
            "request_id": "tablet.climate.legacy-save-failure",
            "correlation_id": "corr.legacy-save-failure",
            "parameters": {"target_temperature": 25.5},
        }
        first = await service.async_execute_legacy_home_targets(**kwargs)
        duplicate = await service.async_execute_legacy_home_targets(**kwargs)
        restarted = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await restarted.async_load()
        after_restart = await restarted.async_execute_legacy_home_targets(**kwargs)

        fact = service._legacy_home_execution_facts["corr.legacy-save-failure"]
        self.assertEqual("unavailable", first["status"])
        self.assertEqual(first["operation_id"], duplicate["operation_id"])
        self.assertEqual(first["operation_id"], after_restart["operation_id"])
        self.assertEqual((0, 0, 0), (
            fact["command_count"], fact["accepted_count"], fact["confirmed_room_count"],
        ))
        self.assertEqual(
            {"temperature": 0, "strategy": 0, "automatic_mode": 0}, fact["changes"],
        )
        self.assertEqual([], contour_store.saved)
        self.assertEqual([], executor.batches)

    async def test_legacy_home_started_reservation_survives_crash_without_redispatch(self) -> None:
        """A binding crash after the real call leaves one non-replayable operation."""

        class CrashAfterStartedStore(SharedAuthenticatedLedgerMemoryStore):
            def __init__(self) -> None:
                super().__init__()
                self.binding_saves = 0

            async def async_save_reliable_scope_bindings(self, bindings: dict[str, object]) -> None:
                self.binding_saves += 1
                if self.binding_saves == 5:
                    raise RuntimeError("injected post-dispatch binding crash")
                await super().async_save_reliable_scope_bindings(bindings)

        runtime, _old_store, contour_store, executor = native_home_target_runtime(
            include_humidifier=False,
        )
        store = CrashAfterStartedStore()
        runtime._direct_control_store = store
        await runtime.async_start()
        service = ClimateTabletService(runtime, store, now_ms=lambda: 1784280005000)
        await service.async_load()
        first_request = {
            "request_id": "tablet.climate.legacy-crash-a",
            "correlation_id": "corr.legacy-crash",
            "parameters": {"target_temperature": 25.5},
        }
        with self.assertRaises(RuntimeError):
            await service.async_execute_legacy_home_targets(**first_request)
        dispatched_batches = len(executor.batches)
        self.assertGreater(dispatched_batches, 0)
        self.assertEqual(1, store._control_revision)
        self.assertEqual(1, len(contour_store.saved))

        restarted_runtime = ClimateRuntime(
            entry_id="entry", configuration=configuration(ClimateControlMode.MANAGED),
            registry_store=runtime._registry_store, contour_store=contour_store,
            strict_ha_call_executor=executor, ha_state_view=runtime._ha_state_view,
            operation_id_factory=lambda: "c" * 32,
            now_ms=lambda: 1784280005000, direct_control_store=store,
        )
        await restarted_runtime.async_start()
        restarted = ClimateTabletService(restarted_runtime, store, now_ms=lambda: 1784280005000)
        await restarted.async_load()
        replay = await restarted.async_execute_legacy_home_targets(
            request_id="tablet.climate.legacy-crash-b",
            correlation_id="corr.legacy-crash",
            parameters={"target_temperature": 25.5},
        )
        self.assertTrue(replay["duplicate"])
        self.assertEqual(dispatched_batches, len(executor.batches))
        self.assertEqual(1, store._control_revision)
        with self.assertRaises(ClimateTabletViolation):
            await restarted.async_execute_legacy_home_targets(
                request_id="tablet.climate.legacy-crash-c",
                correlation_id="corr.legacy-crash",
                parameters={"target_temperature": 25.0},
            )
        # The crash receipt is terminal after restoration. Once its desired
        # intent is no longer active, retention removes the record and its
        # correlation reservation in one persisted transition.
        restarted._desired_intents.clear()
        original_request_id = "tablet.climate.legacy-crash-a"
        restarted._prune_oldest_final()
        await restarted._async_save()
        self.assertNotIn(original_request_id, restarted._records_by_request)
        self.assertNotIn("corr.legacy-crash", restarted._legacy_home_reservations)
        clean_restart = ClimateTabletService(restarted_runtime, store, now_ms=lambda: 1784280005000)
        await clean_restart.async_load()

    async def test_set_room_mode_dispatches_existing_contract_action(self) -> None:
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].append(
            "set_room_mode"
        )
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.mode.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "set_room_mode",
            "room_id": "living",
            "parameters": {"mode": "manual"},
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            {"room_id": "living", "mode": "manual"},
            self.runtime.commands[0],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_set_room_humidity_target_dispatches_typed_room_intent(self) -> None:
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].append(
            "set_room_humidity_target"
        )
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.humidity.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "set_room_humidity_target",
            "room_id": "living",
            "parameters": {"target_humidity": 50},
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            {
                "request_id": "tablet.climate.humidity.1",
                "room_id": "living",
                "target_humidity": 50,
            },
            self.runtime.commands[0],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_minimum_and_strategy_actions_use_their_own_runtime_methods(self) -> None:
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].extend([
            "set_room_min_target", "set_room_target_strategy"
        ])
        minimum = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.minimum.1", "expected_state_revision": self.home["state_revision"],
            "action": "set_room_min_target", "room_id": "living", "parameters": {"minimum_temperature": 18.0},
        }
        strategy = {**minimum, "request_id": "tablet.climate.strategy.1", "action": "set_room_target_strategy", "parameters": {"target_strategy": "soft"}}

        first = await self.service.async_execute(minimum)
        strategy_service = ClimateTabletService(
            self.runtime, MemoryOperationStore(),
            operation_id_factory=lambda: "fedcba9876543210fedcba9876543210",
            now_ms=lambda: self.now,
        )
        second = await strategy_service.async_execute(strategy)

        self.assertEqual("confirmed", first["status"])
        self.assertEqual("confirmed", second["status"])
        self.assertEqual(18.0, self.runtime.commands[0]["minimum_temperature"])
        self.assertEqual("soft", self.runtime.commands[1]["target_strategy"])

    async def test_turn_room_off_uses_a_dedicated_runtime_method(self) -> None:
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].append("turn_room_off")
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.off.1", "expected_state_revision": self.home["state_revision"],
            "action": "turn_room_off", "room_id": "living", "parameters": {},
        }
        receipt = await self.service.async_execute(request)
        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual("turn_room_off", self.runtime.commands[0]["action"])

    async def test_set_device_mode_dispatches_durable_manual_exclusion(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device["mode"] = "automatic"
        device["control"] = {
            "enabled": True,
            "allowed_actions": ["set_device_mode"],
            "actions": ["set_device_mode"],
            "blocked_reasons": [],
        }
        request = {
            "contract": {
                "name": "hausman-hub-climate-action-request",
                "version": 1,
            },
            "request_id": "tablet.climate.device-mode.1",
            "expected_state_revision": self.home["state_revision"],
            "action": "set_device_mode",
            "room_id": "living",
            "parameters": {
                "device_id": device["id"],
                "mode": "manual",
            },
        }

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            {
                "room_id": "living",
                "device_id": device["id"],
                "mode": "manual",
            },
            self.runtime.commands[0],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_pending_operation_confirms_from_read_back_without_reexecution(self) -> None:
        self.runtime.result_status = ContourApplyStatus.PENDING
        request = action_request(self.home["state_revision"])
        pending = await self.service.async_execute(request)
        self.runtime.home["state_revision"] += 1
        self.runtime.home["rooms"][0]["target_temperature"] = 23.5
        temporary = self.runtime.home["contours"][0]["rooms"][0][
            "temporary_temperature"
        ]
        temporary.update(
            {
                "active": True,
                "temperature": 23.5,
                "ends": "next_schedule_change",
                "ends_at": "2026-08-05T23:00:00+00:00",
            }
        )

        confirmed = await self.service.async_operation(pending["operation_id"])

        self.assertEqual("confirmed", confirmed["status"])
        self.assertTrue(confirmed["read_back"]["matched"])
        self.assertEqual(1, len(self.runtime.commands))
        contract_validator("climate-operation-receipt.schema.json").validate(
            confirmed
        )

    async def test_pending_minimum_and_strategy_confirm_only_from_fresh_readback(self) -> None:
        self.runtime.result_status = ContourApplyStatus.PENDING
        self.runtime.home["rooms"][0]["control"]["allowed_actions"].extend(["set_room_min_target", "set_room_target_strategy"])
        request = {"contract": {"name": "hausman-hub-climate-action-request", "version": 1}, "request_id": "tablet.climate.min.poll", "expected_state_revision": self.home["state_revision"], "action": "set_room_min_target", "room_id": "living", "parameters": {"minimum_temperature": 18.0}}
        pending = await self.service.async_execute(request)
        self.runtime.home["state_revision"] += 1
        self.runtime.home["rooms"][0]["minimum_temperature"] = 18.0
        confirmed = await self.service.async_operation(pending["operation_id"])
        self.assertEqual("confirmed", confirmed["status"])
        self.assertEqual(1, len(self.runtime.commands))

    async def test_restart_restores_final_receipt_without_reexecution(self) -> None:
        request = action_request(self.home["state_revision"])
        original = await self.service.async_execute(request)
        restarted_runtime = FakeRuntime(self.home)
        restarted = ClimateTabletService(restarted_runtime, self.store)

        await restarted.async_load()
        duplicate = await restarted.async_execute(request)

        self.assertEqual(original["operation_id"], duplicate["operation_id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual([], restarted_runtime.commands)

    async def test_legacy_fingerprint_migrates_once_without_reexecution(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.legacy.1"
        )
        original = await self.service.async_execute(request)
        legacy = copy.deepcopy(self.store.payload)
        legacy["version"] = 5
        legacy["records"][0]["fingerprint"] = legacy_tablet_fingerprint(request)
        store = MemoryOperationStore(legacy)
        first_runtime = FakeRuntime(self.home)
        first_restart = ClimateTabletService(first_runtime, store)

        await first_restart.async_load()
        migrated = store.payload
        self.assertEqual(6, migrated["version"])
        self.assertEqual(
            parse_climate_tablet_action(request).fingerprint,
            migrated["records"][0]["fingerprint"],
        )
        duplicate = await first_restart.async_execute(request)
        self.assertEqual(original["operation_id"], duplicate["operation_id"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual([], first_runtime.commands)

        second_runtime = FakeRuntime(self.home)
        second_restart = ClimateTabletService(second_runtime, store)
        await second_restart.async_load()
        duplicate_after_restart = await second_restart.async_execute(request)
        self.assertTrue(duplicate_after_restart["duplicate"])
        self.assertEqual([], second_runtime.commands)

    async def test_forged_legacy_fingerprint_remains_rejected(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.legacy-forged"
        )
        await self.service.async_execute(request)
        forged = copy.deepcopy(self.store.payload)
        forged["version"] = 5
        forged["records"][0]["fingerprint"] = "f" * 64

        restored = ClimateTabletService(
            FakeRuntime(self.home), MemoryOperationStore(forged)
        )
        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_legacy_fingerprint_rejects_other_valid_receipt_correlation(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.legacy-correlation"
        )
        await self.service.async_execute(request)
        forged = copy.deepcopy(self.store.payload)
        forged["version"] = 5
        forged["records"][0]["fingerprint"] = legacy_tablet_fingerprint(request)
        forged["records"][0]["receipt"]["correlation_id"] = (
            "corr.tablet.climate.other"
        )
        store = MemoryOperationStore(forged)
        restored = ClimateTabletService(FakeRuntime(self.home), store)

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()
        self.assertEqual(5, store.payload["version"])
        self.assertEqual(
            legacy_tablet_fingerprint(request),
            store.payload["records"][0]["fingerprint"],
        )

    async def test_pending_reservation_times_out_after_restart_without_reexecution(self) -> None:
        request = action_request(self.home["state_revision"])
        completed = await self.service.async_execute(request)
        pending_store = MemoryOperationStore(self.store.saved[0])
        restarted_runtime = FakeRuntime(self.home)
        now = self.now + 60_000
        restarted = ClimateTabletService(
            restarted_runtime,
            pending_store,
            now_ms=lambda: now,
        )

        await restarted.async_load()
        snapshot = await restarted.async_snapshot()
        receipt = await restarted.async_operation(completed["operation_id"])

        self.assertEqual([], snapshot["active_operations"])
        self.assertEqual("timed_out", receipt["status"])
        self.assertTrue(receipt["final"])
        self.assertEqual([], restarted_runtime.commands)
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliability_pending_attempt_times_out_after_restart(self) -> None:
        self.runtime.result_status = ContourApplyStatus.PENDING
        request = action_request(self.home["state_revision"])
        request.update(reliability_profile="climate_reliability_v1", expected_control_revision=0)
        pending = await self.service.async_execute(request)
        restarted = ClimateTabletService(
            FakeRuntime(self.home), MemoryOperationStore(self.store.payload),
            now_ms=lambda: self.now + 60_000,
        )
        await restarted.async_load()
        receipt = await restarted.async_operation(pending["operation_id"])

        self.assertEqual("timed_out", receipt["status"])
        self.assertEqual(["living_ac"], receipt["action_snapshot"]["resolved_scope"]["device_ids"])
        self.assertEqual("accepted_timeout", receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]["execution_state"])
        self.assertTrue(receipt["final"])
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliability_dispatch_checkpoint_survives_final_save_failure(self) -> None:
        store = CrashOnSaveStore(fail_on_save=3)
        service = ClimateTabletService(
            self.runtime, store,
            operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
            now_ms=lambda: self.now,
        )
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        with self.assertRaises(RuntimeError):
            await service.async_execute(request)
        self.assertEqual(1, len(self.runtime.commands))

        restarted = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(store.payload),
            now_ms=lambda: self.now + 60_000,
        )
        await restarted.async_load()
        receipt = await restarted.async_operation("0123456789abcdef0123456789abcdef")

        self.assertEqual("partial", receipt["status"])
        self.assertTrue(receipt["final"])
        self.assertTrue(receipt["read_back"]["attempted"])
        self.assertEqual(
            "dispatched_not_accepted",
            receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]["execution_state"],
        )
        self.assertEqual(
            0,
            receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]["accepted_count"],
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliability_checkpoint_save_failure_never_calls_runtime(self) -> None:
        store = CrashOnSaveStore(fail_on_save=2)
        service = ClimateTabletService(
            self.runtime, store,
            operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
            now_ms=lambda: self.now,
        )
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        with self.assertRaises(RuntimeError):
            await service.async_execute(request)
        self.assertEqual([], self.runtime.commands)

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(store.payload)
        )
        await restored.async_load()
        self.assertEqual(
            "pending_dispatch",
            store.payload["records"][0]["dispatch_ledger"]["state"],
        )

    async def test_initial_main_save_failure_closes_live_service_until_restart(self) -> None:
        store = CrashOnSaveStore(fail_on_save=1)
        service = ClimateTabletService(
            self.runtime, store,
            operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
            now_ms=lambda: self.now,
        )
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        with self.assertRaises(RuntimeError):
            await service.async_execute(request)
        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_execute(request)
        self.assertEqual([], self.runtime.commands)
        self.assertIsNone(store.payload)

    async def test_reliability_version_six_requires_dispatch_ledger(self) -> None:
        request = action_request(self.home["state_revision"])
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["records"][0].pop("dispatch_ledger")

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )
        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_pending_reservation_survives_restart_while_runtime_is_disabled(self) -> None:
        request = action_request(self.home["state_revision"])
        completed = await self.service.async_execute(request)
        pending_store = MemoryOperationStore(self.store.saved[0])
        restarted_runtime = FakeRuntime(self.home)
        restarted_runtime.configuration = SimpleNamespace(
            mode="read-only",
            climate_bridge_mode=ClimateControlMode.DISABLED,
        )
        restarted = ClimateTabletService(
            restarted_runtime,
            pending_store,
            now_ms=lambda: self.now + 30_000,
        )

        await restarted.async_load()
        snapshot = await restarted.async_snapshot()
        duplicate = await restarted.async_execute(request)

        self.assertEqual(
            completed["operation_id"],
            snapshot["active_operations"][0]["operation_id"],
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("pending", duplicate["status"])
        self.assertEqual([], restarted_runtime.commands)
        contract_validator("climate-runtime.schema.json").validate(snapshot)

    async def test_conflicting_request_id_is_rejected_without_command(self) -> None:
        await self.service.async_execute(action_request(self.home["state_revision"]))

        with self.assertRaisesRegex(ClimateTabletViolation, "already used"):
            await self.service.async_execute(
                action_request(self.home["state_revision"], target=24.0)
            )
        self.assertEqual(1, len(self.runtime.commands))

    async def test_stale_revision_is_rejected_before_reservation(self) -> None:
        with self.assertRaisesRegex(ClimateTabletViolation, "revision changed"):
            await self.service.async_execute(
                action_request(self.home["state_revision"] + 1)
            )

        self.assertEqual([], self.runtime.commands)
        self.assertEqual([], self.store.saved)

    async def test_shadow_runtime_reads_state_without_exposing_commands(self) -> None:
        self.runtime.configuration = SimpleNamespace(
            mode="shadow",
            climate_bridge_mode=ClimateControlMode.DISABLED,
        )

        snapshot = await self.service.async_snapshot()

        self.assertEqual("shadow", snapshot["phase"])
        self.assertEqual("living", snapshot["rooms"][0]["id"])
        self.assertFalse(snapshot["commands_enabled"])
        self.assertEqual([], self.runtime.commands)

        with self.assertRaises(ClimateTabletViolation) as raised:
            await self.service.async_execute(
                action_request(snapshot["state_revision"])
            )
        self.assertEqual("climate_shadow_only", raised.exception.code)
        self.assertEqual([], self.store.saved)
        self.assertEqual([], self.runtime.commands)

    async def test_unknown_operation_is_not_found(self) -> None:
        with self.assertRaises(ClimateTabletOperationNotFound):
            await self.service.async_operation("f" * 32)

    async def test_external_off_is_an_explicit_recovery_participation_reason(self) -> None:
        """An attributed external shutdown has the same explicit recovery path."""
        from custom_components.hausman_hub.application.climate_tablet import (
            _recovery_preflight, _with_reliability_projection,
        )

        snapshot = await self.service.async_snapshot()
        device = snapshot["rooms"][0]["devices"][0]
        device["mode"] = "manual"
        device["manual_reason"] = "external_off"
        projected = _with_reliability_projection(snapshot, {}, snapshot["control_revision"])
        participation = projected["rooms"][0]["devices"][0]["participation"]
        self.assertEqual("external_off", participation["reason"])
        self.assertEqual("return_to_contour", participation["recovery"])
        self.assertEqual(["living_ac"], _recovery_preflight(projected, "living")["resolved_device_ids"])

    async def test_recovery_replays_durable_scoped_receipt_without_redispatch(self) -> None:
        preflight, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.1"
        )
        receipt = await self.service.async_recover_room("living", request)
        duplicate = await self.service.async_recover_room("living", request)

        self.assertTrue(receipt["confirmed"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("recover", self.runtime.commands[0]["action"])
        self.assertEqual(preflight["desired_snapshot"]["living_ac"], self.runtime.commands[0]["desired"])
        self.assertEqual(receipt["operation_id"], (await self.service.async_recovery_operation(receipt["operation_id"]))["operation_id"])
        self.assertEqual(preflight["control_revision"] + 1, receipt["resulting_control_revision"])
        self.assertEqual(receipt["resulting_control_revision"], (await self.service.async_snapshot())["control_revision"])
        self.assertEqual(receipt["resulting_control_revision"], duplicate["resulting_control_revision"])
        contract_validator("climate-room-recovery-receipt-v2.schema.json").validate(receipt)

    async def test_recovery_confirmation_uses_device_observation_not_dispatch_clock(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.device-evidence"
        )
        receipt = await self.service.async_recover_room("living", request)

        leaf = receipt["outcomes"]["living_ac"]
        evidence = receipt["read_back"]["evidence"]["devices"]["living_ac"]
        self.assertEqual(self.now + 1, evidence["observed_at"])
        self.assertGreater(evidence["observed_at"], leaf["dispatched_at"])
        self.assertEqual(evidence["observed_at"], receipt["read_back"]["observed_at"])

    async def test_recovery_preflight_requires_device_control_authority(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device["control"]["enabled"] = False
        device["control"]["allowed_actions"] = []
        device["control"]["blocked_reasons"] = ["action_unsupported"]

        with self.assertRaises(ClimateTabletViolation):
            await self.service.async_recovery_v2_preflight("living")
        self.assertEqual([], self.runtime.commands)

    async def test_recovery_preflight_merges_private_source_proof(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device.pop("observed_at", None)
        self.runtime.recovery_private_metadata[("living", "living_ac")] = {
            "manual_reason": "user_excluded",
            "source_observed_at": self.now - 1,
            "reported_target_temperature": 24,
            "reported_target_humidity": None,
        }

        preflight = await self.service.async_recovery_v2_preflight("living")

        self.assertEqual(
            self.now - 1,
            preflight["desired_snapshot"]["living_ac"]["source_observed_at"],
        )

    async def test_recovery_preflight_keeps_public_metadata_when_private_proof_is_partial(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device.pop("observed_at", None)
        self.runtime.recovery_private_metadata[("living", "living_ac")] = {
            "source_observed_at": self.now - 1,
        }

        preflight = await self.service.async_recovery_v2_preflight("living")

        self.assertEqual(["living_ac"], preflight["resolved_device_ids"])
        self.assertEqual(24, preflight["desired_snapshot"]["living_ac"]["target_temperature"])

    async def test_recovery_preflight_rejects_malformed_private_source_proof(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device.pop("observed_at", None)
        self.runtime.recovery_private_metadata[("living", "living_ac")] = {
            "source_observed_at": "malformed",
        }

        with self.assertRaises(ClimateTabletUnavailable):
            await self.service.async_recovery_v2_preflight("living")

        self.assertEqual({}, self.service._recovery_preflights)

    async def test_recovery_snapshot_keeps_offline_private_null_source(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device["available"] = False
        device.pop("observed_at", None)
        self.runtime.recovery_private_metadata[("living", "living_ac")] = {
            "source_observed_at": None,
            "reported_target_temperature": 17.0,
            "reported_target_humidity": 20.0,
        }

        snapshot = await self.service.async_snapshot()

        self.assertFalse(snapshot["rooms"][0]["devices"][0]["available"])

    async def test_recovery_preflight_rejects_available_device_without_source_proof(self) -> None:
        device = self.runtime.home["rooms"][0]["devices"][0]
        device.pop("observed_at", None)
        self.runtime.recovery_private_metadata[("living", "living_ac")] = {
            "source_observed_at": None,
        }

        with self.assertRaises(ClimateTabletViolation):
            await self.service.async_recovery_v2_preflight("living")

    async def test_recovery_preflight_is_bounded_and_single_use(self) -> None:
        first = await self.service.async_recovery_v2_preflight("living")
        second = await self.service.async_recovery_v2_preflight("living")
        self.assertNotEqual(first["snapshot_token"], second["snapshot_token"])
        self.assertEqual([second["snapshot_token"]], list(self.service._recovery_preflights))

        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.token-consumed"
        )
        await self.service.async_recover_room("living", request)
        self.assertNotIn(request["snapshot_token"], self.service._recovery_preflights)

    async def test_recovery_preflight_survives_restart_before_post(self) -> None:
        preflight = await self.service.async_recovery_v2_preflight("living")
        restarted = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload),
            now_ms=lambda: self.now,
        )

        await restarted.async_load()

        self.assertIn(preflight["snapshot_token"], restarted._recovery_preflights)

    async def test_recovery_uses_persisted_desired_target_after_restart_not_reported_state(self) -> None:
        snapshot = await self.service.async_snapshot()
        target = action_request(snapshot["state_revision"], request_id="tablet.climate.intent.1", target=23.5)
        target.update({
            "reliability_profile": "climate_reliability_v1",
            "expected_control_revision": snapshot["control_revision"],
        })
        await self.service.async_execute(target)
        # The adapter deliberately leaves the reported value at 24.0.  The
        # saved 23.5 intent is the only recovery authority after restart.
        self.assertEqual(24, self.runtime.home["rooms"][0]["target_temperature"])
        restarted_runtime = FakeRuntime(self.runtime.home)
        restarted = ClimateTabletService(restarted_runtime, MemoryOperationStore(self.store.payload))
        await restarted.async_load()
        preflight, request = await self._recovery_v2_request(
            service=restarted,
            request_id="tablet.climate.recovery.persisted-intent",
        )
        self.assertEqual(23.5, preflight["desired_snapshot"]["living_ac"]["target_temperature"])
        self.assertIsNone(preflight["desired_snapshot"]["living_ac"]["target_humidity"])
        receipt = await restarted.async_recover_room("living", request)

        self.assertEqual(23.5, restarted_runtime.commands[-1]["desired"]["target_temperature"])
        self.assertEqual(23.5, receipt["desired_snapshot"]["living_ac"]["target_temperature"])
        self.assertEqual("unknown", receipt["status"])
        self.assertEqual({}, receipt["read_back"]["evidence"])

        # The one-step return consumed manual ownership. A fresh recovery is
        # not advertised for an automatic device, even if confirmation is
        # still pending.
        with self.assertRaises(ClimateTabletViolation):
            await restarted.async_recovery_v2_preflight("living")
        self.assertEqual(1, len(restarted_runtime.commands))

    async def test_humidity_only_intent_keeps_temperature_null_after_restart(self) -> None:
        self.runtime.home["rooms"][0]["devices"].append(
            {
                "id": "living_humidifier", "name": "Увлажнитель в гостиной",
                "kind": "humidifier", "control_scope": "managed",
                "capabilities": ["target_humidity", "power"],
                "available": True, "state": "working",
            }
        )
        snapshot = await self.service.async_snapshot()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.humidity-null-axis",
            "expected_state_revision": snapshot["state_revision"],
            "expected_control_revision": snapshot["control_revision"],
            "reliability_profile": "climate_reliability_v1",
            "action": "set_room_humidity_target", "room_id": "living",
            "parameters": {"target_humidity": 50},
        }
        await self.service.async_execute(request)
        restarted = ClimateTabletService(FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload))
        await restarted.async_load()
        restored = await restarted.async_snapshot()
        device = restored["rooms"][0]["devices"][0]
        self.assertIsNone(device["desired_target_temperature"])
        self.assertEqual(50, device["desired_target_humidity"])

    async def test_durable_intent_merges_target_and_humidity_patches_after_restart(self) -> None:
        self.runtime.home["rooms"][0]["devices"].append(
            {
                "id": "living_humidifier", "name": "Увлажнитель в гостиной",
                "kind": "humidifier", "control_scope": "managed",
                "capabilities": ["target_humidity", "power"],
                "available": True, "state": "working",
            }
        )
        self.runtime.home["rooms"][0]["control"]["allowed_actions"] += [
            "set_room_humidity_target",
        ]
        first = await self.service.async_snapshot()
        target = action_request(
            first["state_revision"], request_id="tablet.climate.merge.target", target=23.5,
        )
        target.update({"reliability_profile": "climate_reliability_v1",
                       "expected_control_revision": first["control_revision"]})
        await self.service.async_execute(target)
        self.service._operation_id_factory = lambda: "1123456789abcdef0123456789abcdef"
        second = await self.service.async_snapshot()
        humidity = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.merge.humidity",
            "expected_state_revision": second["state_revision"],
            "expected_control_revision": second["control_revision"],
            "reliability_profile": "climate_reliability_v1",
            "action": "set_room_humidity_target", "room_id": "living",
            "parameters": {"target_humidity": 50},
        }
        await self.service.async_execute(humidity)
        restarted = ClimateTabletService(FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload))
        await restarted.async_load()
        device = (await restarted.async_snapshot())["rooms"][0]["devices"][0]
        self.assertEqual(23.5, device["desired_target_temperature"])
        self.assertEqual(50, device["desired_target_humidity"])
        self.assertEqual(2, device["control_revision"])


    async def test_recovery_crash_after_physical_call_never_redispatches_after_restart(self) -> None:
        # The v2 preflight is itself durable.  The fourth save is after the
        # physical call but before accepted confirmation can be persisted.
        store = CrashOnSaveStore(fail_on_save=4)
        service = ClimateTabletService(self.runtime, store)
        _, request = await self._recovery_v2_request(
            service=service,
            request_id="tablet.climate.recovery.crash",
        )
        with self.assertRaisesRegex(RuntimeError, "injected storage crash"):
            await service.async_recover_room("living", request)
        self.assertEqual(1, len(self.runtime.commands))

        restored = ClimateTabletService(self.runtime, store)
        await restored.async_load()
        replay = await restored.async_recover_room("living", request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual("unknown", replay["status"])
        self.assertEqual(1, len(self.runtime.commands))

    async def test_recovery_crash_before_call_has_no_service_call_or_implicit_resume(self) -> None:
        # First save persists the v2 preflight, second the reservation and
        # third the started boundary before any physical call.
        store = CrashOnSaveStore(fail_on_save=3)
        service = ClimateTabletService(self.runtime, store)
        _, request = await self._recovery_v2_request(
            service=service,
            request_id="tablet.climate.recovery.before-call",
        )
        with self.assertRaisesRegex(RuntimeError, "injected storage crash"):
            await service.async_recover_room("living", request)
        self.assertEqual([], self.runtime.commands)

        restored = ClimateTabletService(self.runtime, store)
        await restored.async_load()
        replay = await restored.async_recover_room("living", request)
        self.assertTrue(replay["duplicate"])
        self.assertEqual("pending", replay["status"])
        self.assertEqual([], self.runtime.commands)
        # Even a coincidentally matching later projection cannot prove a call
        # that never crossed the durable dispatch checkpoint.
        self.runtime.home["rooms"][0]["devices"][0]["mode"] = "automatic"
        operation = await restored.async_recovery_operation(replay["operation_id"])
        self.assertEqual("pending", operation["status"])
        self.assertEqual("pending_dispatch", operation["outcomes"]["living_ac"]["execution_state"])
        self.assertEqual([], self.runtime.commands)

    async def test_recovery_persists_each_sibling_and_reports_partial_readback(self) -> None:
        second = copy.deepcopy(self.runtime.home["rooms"][0]["devices"][0])
        second["id"] = "living_ac_2"
        second["mode"] = "manual"
        self.runtime.home["rooms"][0]["devices"].append(second)
        second["available"] = False
        preflight, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.siblings",
            device_ids=["living_ac", "living_ac_2"],
        )
        receipt = await self.service.async_recover_room("living", request)

        self.assertEqual("partial", receipt["status"])
        self.assertEqual(preflight["control_revision"] + 1, receipt["resulting_control_revision"])
        self.assertEqual(receipt["resulting_control_revision"], (await self.service.async_snapshot())["control_revision"])
        self.assertEqual("applied", receipt["outcomes"]["living_ac"]["execution_state"])
        self.assertEqual("deferred", receipt["outcomes"]["living_ac_2"]["status"])
        self.assertEqual(2, len(self.runtime.commands))
        contract_validator("climate-room-recovery-receipt-v2.schema.json").validate(receipt)
        duplicate = await self.service.async_recover_room("living", request)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(2, len(self.runtime.commands))

        # Both leaves returned to automatic ownership.  A terminal partial
        # result must not advertise another physical recovery preflight.
        with self.assertRaises(ClimateTabletViolation):
            await self.service.async_recovery_v2_preflight("living")
        self.assertEqual(2, len(self.runtime.commands))

    async def test_recovery_revision_persists_restart_and_rejects_stale_token_without_increment(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.revision.1"
        )
        receipt = await self.service.async_recover_room("living", request)
        restarted = ClimateTabletService(FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload))
        await restarted.async_load()
        self.assertEqual(receipt["resulting_control_revision"], (await restarted.async_snapshot())["control_revision"])
        from custom_components.hausman_hub.application.climate_tablet import (
            _recovery_request_fingerprint,
        )

        stale = dict(request)
        stale["request_id"] = "tablet.climate.recovery.revision.stale"
        stale["request_fingerprint"] = _recovery_request_fingerprint(stale)
        with self.assertRaisesRegex(ClimateTabletViolation, "preflight changed"):
            await restarted.async_recover_room("living", stale)
        self.assertEqual(receipt["resulting_control_revision"], (await restarted.async_snapshot())["control_revision"])

    async def test_recovery_loader_rejects_leaf_without_signed_dispatch_evidence(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.legacy-evidence"
        )
        await self.service.async_recover_room("living", request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["recoveries"][0]["ledger"]["living_ac"].pop("dispatched_at", None)
        damaged["recoveries"][0]["ledger"]["living_ac"]["ledger_state"] = "started"
        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )
        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_recovery_loader_rejects_corrupted_receipt_snapshot(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.corrupt-receipt"
        )
        await self.service.async_recover_room("living", request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["recoveries"][0]["receipt"]["desired_snapshot"]["living_ac"][
            "target_temperature"
        ] = 25.0

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_recovery_loader_rejects_invalid_receipt_contract(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.corrupt-contract"
        )
        await self.service.async_recover_room("living", request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["recoveries"][0]["receipt"]["contract"]["version"] = 999

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_recovery_loader_rejects_control_revision_rollback(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.corrupt-revision"
        )
        receipt = await self.service.async_recover_room("living", request)
        self.assertEqual(1, receipt["resulting_control_revision"])
        damaged = copy.deepcopy(self.store.payload)
        damaged["control_revision"] = 0

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_recovery_loader_rejects_evidence_not_newer_than_dispatch(self) -> None:
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.corrupt-evidence"
        )
        await self.service.async_recover_room("living", request)
        damaged = copy.deepcopy(self.store.payload)
        receipt = damaged["recoveries"][0]["receipt"]
        source_observed_at = receipt["desired_snapshot"]["living_ac"][
            "source_observed_at"
        ]
        evidence = receipt["read_back"]["evidence"]["devices"]["living_ac"]
        evidence["observed_at"] = source_observed_at
        receipt["read_back"]["observed_at"] = source_observed_at
        receipt["updated_at"] = source_observed_at

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_recovery_confirmation_requires_newer_observation_than_source_snapshot(self) -> None:
        self.runtime.home["rooms"][0]["devices"][0]["observed_at"] = self.now + 10
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.source-order"
        )

        receipt = await self.service.async_recover_room("living", request)

        self.assertEqual("unknown", receipt["status"])
        self.assertEqual({}, receipt["read_back"]["evidence"])

    async def test_recovery_pre_dispatch_runtime_error_is_honest_zero_command_leaf(self) -> None:
        error = RuntimeError("runtime gate changed")
        error.recovery_pre_dispatch = True
        self.runtime.recovery_error = error
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.runtime-gate"
        )

        receipt = await self.service.async_recover_room("living", request)

        leaf = receipt["outcomes"]["living_ac"]
        self.assertEqual(0, leaf["command_count"])
        self.assertEqual(0, leaf["accepted_count"])
        self.assertEqual("blocked_before_dispatch", leaf["execution_state"])
        self.assertEqual([], self.runtime.commands)

    async def test_recovery_post_dispatch_runtime_error_remains_accepted_unverified(self) -> None:
        error = RuntimeError("post-dispatch persistence failed")
        error.recovery_accepted_after_dispatch = True
        self.runtime.recovery_post_dispatch_error = error
        _, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.post-dispatch"
        )

        receipt = await self.service.async_recover_room("living", request)

        leaf = receipt["outcomes"]["living_ac"]
        self.assertEqual(1, leaf["command_count"])
        self.assertEqual(1, leaf["accepted_count"])
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertEqual(1, len(self.runtime.commands))

    async def test_recovery_ambiguous_dispatch_blocks_new_request_id(self) -> None:
        self.runtime.recovery_error = RuntimeError("executor acknowledgement lost")
        _, first = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.ambiguous-first"
        )
        receipt = await self.service.async_recover_room("living", first)
        self.assertEqual("dispatched_not_accepted", receipt["outcomes"]["living_ac"]["execution_state"])

        _, successor = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.ambiguous-successor"
        )
        with self.assertRaisesRegex(ClimateTabletViolation, "already in progress"):
            await self.service.async_recover_room("living", successor)
        self.assertEqual([], self.runtime.commands)

    async def test_recovery_pre_dispatch_rejection_does_not_increment_revision(self) -> None:
        from custom_components.hausman_hub.application.climate_tablet import (
            _recovery_request_fingerprint,
        )

        preflight, request = await self._recovery_v2_request(
            request_id="tablet.climate.recovery.invalid-token"
        )
        request["snapshot_token"] = "recovery.invalid.000000000000000000000000"
        request["request_fingerprint"] = _recovery_request_fingerprint(request)
        with self.assertRaisesRegex(ClimateTabletViolation, "preflight changed"):
            await self.service.async_recover_room("living", request)
        self.assertEqual(preflight["control_revision"], (await self.service.async_snapshot())["control_revision"])

    async def test_damaged_store_fails_closed(self) -> None:
        service = ClimateTabletService(
            self.runtime,
            MemoryOperationStore({"version": 1, "records": [{"unsafe": True}]}),
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_load()

    async def test_inconsistent_persisted_receipt_fails_closed(self) -> None:
        await self.service.async_execute(action_request(self.home["state_revision"]))
        damaged = copy.deepcopy(self.store.payload)
        damaged["records"][0]["receipt"]["confirmed"] = False
        service = ClimateTabletService(self.runtime, MemoryOperationStore(damaged))

        with self.assertRaises(ClimateTabletUnavailable):
            await service.async_load()

    async def test_reliable_receipt_loader_rejects_forged_fingerprint_and_action(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.reliable-corrupt"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        receipt = damaged["records"][0]["receipt"]
        receipt["request_fingerprint"] = "f" * 64
        receipt["action_parameters"]["target_temperature"] = 25.0

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_receipt_loader_rejects_forged_leaf_evidence(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.reliable-evidence"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["records"][0]["receipt"]["outcomes"]["rooms"]["living"][
            "devices"
        ]["living_ac"]["evidence"]["observed_at"] = 0

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_receipt_loader_rejects_foreign_frozen_scope(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.foreign-scope"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        receipt = damaged["records"][0]["receipt"]
        scope = receipt["action_snapshot"]["resolved_scope"]
        scope.update(
            room_ids=["kitchen"],
            device_ids=["kitchen_ac"],
            devices_by_room=[{"room_id": "kitchen", "device_ids": ["kitchen_ac"]}],
        )
        receipt["intent"]["resolved_scope"] = copy.deepcopy(scope)
        receipt["intent"]["scope_fingerprint"] = climate_tablet_module._canonical_fingerprint(scope)

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_receipt_loader_rejects_invented_scope_device(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.invented-scope"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        record = damaged["records"][0]
        receipt = record["receipt"]
        scope = receipt["action_snapshot"]["resolved_scope"]
        scope["device_ids"] = ["living_fake"]
        scope["devices_by_room"] = [
            {"room_id": "living", "device_ids": ["living_fake"]}
        ]
        room = receipt["outcomes"]["rooms"]["living"]
        leaf = room["devices"].pop("living_ac")
        room["devices"]["living_fake"] = leaf
        receipt["intent"]["resolved_scope"] = copy.deepcopy(scope)
        receipt["intent"]["scope_fingerprint"] = climate_tablet_module._canonical_fingerprint(scope)
        sources = record["dispatch_ledger"].get("pre_dispatch_sources")
        if isinstance(sources, dict) and isinstance(sources.get("living"), dict):
            source = sources["living"].pop("living_ac")
            sources["living"]["living_fake"] = source

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_scope_binding_rejects_coordinated_ledger_tamper(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.scope-mac"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        restored_store = MemoryOperationStore(damaged)
        record = damaged["records"][0]
        receipt = record["receipt"]
        scope = receipt["action_snapshot"]["resolved_scope"]
        scope["device_ids"] = ["living_fake"]
        scope["devices_by_room"] = [
            {"room_id": "living", "device_ids": ["living_fake"]}
        ]
        room = receipt["outcomes"]["rooms"]["living"]
        room["devices"]["living_fake"] = room["devices"].pop("living_ac")
        receipt["intent"]["resolved_scope"] = copy.deepcopy(scope)
        receipt["intent"]["scope_fingerprint"] = climate_tablet_module._canonical_fingerprint(scope)
        binding = restored_store._scope_bindings[request["request_id"]]
        binding["resolved_scope"] = copy.deepcopy(scope)
        binding["scope_fingerprint"] = climate_tablet_module._canonical_fingerprint(scope)

        restored = ClimateTabletService(FakeRuntime(self.runtime.home), restored_store)

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_checkpoint_rejects_terminal_receipt_tamper(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.receipt-mac"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        restored_store = MemoryOperationStore(self.store.payload)
        receipt = restored_store.payload["records"][0]["receipt"]
        receipt["updated_at"] += 1

        restored = ClimateTabletService(FakeRuntime(self.runtime.home), restored_store)

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_scope_survives_inventory_change_after_dispatch(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.scope-history"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        receipt = await self.service.async_execute(request)
        extra = copy.deepcopy(self.runtime.home["rooms"][0]["devices"][0])
        extra["id"] = "living_ac_2"
        self.runtime.home["rooms"][0]["devices"].append(extra)

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload)
        )
        await restored.async_load()

        restored_receipt = restored._records_by_request[
            request["request_id"]
        ].receipt
        self.assertEqual(
            receipt["action_snapshot"]["resolved_scope"],
            restored_receipt["action_snapshot"]["resolved_scope"],
        )

    async def test_reliable_scope_binding_failure_never_publishes_main_record(self) -> None:
        store = ScopeBindingSaveFailureStore()
        service = ClimateTabletService(
            self.runtime,
            store,
            operation_id_factory=lambda: "0123456789abcdef0123456789abcdef",
            now_ms=lambda: self.now,
        )
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.scope-save-failure"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        with self.assertRaises(RuntimeError):
            await service.async_execute(request)

        self.assertIsNone(store.payload)
        self.assertEqual([], self.runtime.commands)

    async def test_reliable_receipt_loader_rejects_boolean_leaf_counters(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.boolean-ledger"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["records"][0]["receipt"]["outcomes"]["rooms"]["living"][
            "devices"
        ]["living_ac"]["command_count"] = True

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_receipt_loader_rejects_mismatched_reported_leaf_value(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.reliable-reported"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        evidence = damaged["records"][0]["receipt"]["outcomes"]["rooms"]["living"][
            "devices"
        ]["living_ac"]["evidence"]
        evidence["reported_target_temperature"] = 25.0
        evidence["observed_actual"]["reported_target_temperature"] = 25.0

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_reliable_aggregate_success_without_device_readback_stays_pending(self) -> None:
        async def aggregate_only(payload: object, now: object) -> object:
            del now
            self.runtime.commands.append(copy.deepcopy(payload))
            return SimpleNamespace(
                status=ContourApplyStatus.CONFIRMED,
                command_count=1,
                confirmed_room_count=1,
                accepted_count=1,
            )

        self.runtime.async_temporary_temperature = aggregate_only
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.aggregate-only"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]

        self.assertEqual("partial", receipt["status"])
        self.assertFalse(receipt["confirmed"])
        self.assertFalse(receipt["final"])
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertNotIn("evidence", leaf)

    async def test_reliable_clear_override_requires_observed_inactive_override(self) -> None:
        async def aggregate_only(payload: object, now: object) -> object:
            del now
            self.runtime.commands.append(copy.deepcopy(payload))
            return SimpleNamespace(
                status=ContourApplyStatus.CONFIRMED,
                command_count=1,
                confirmed_room_count=1,
                accepted_count=1,
            )

        self.runtime.home["rooms"][0]["control"]["allowed_actions"].append(
            "clear_room_override"
        )
        self.runtime.home["contours"][0]["rooms"][0]["temporary_temperature"].update(
            active=True, temperature=23.5
        )
        self.runtime.async_temporary_temperature = aggregate_only
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.clear-proof",
            "expected_state_revision": self.home["state_revision"],
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "clear_room_override",
            "room_id": "living",
            "parameters": {},
        }

        receipt = await self.service.async_execute(request)

        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("partial", receipt["status"])
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertNotIn("evidence", leaf)

    async def test_reliable_synchronization_requires_device_synchronization_readback(self) -> None:
        self.runtime.home["rooms"][0]["devices"][0]["available"] = False
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.sync-proof",
            "expected_state_revision": self.home["state_revision"],
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "synchronize_home",
            "room_id": None,
            "parameters": {},
        }

        receipt = await self.service.async_execute(request)

        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("partial", receipt["status"])
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertNotIn("evidence", leaf)

    async def test_reliable_synchronization_rejects_available_device_with_stale_axis(self) -> None:
        self.runtime.home["rooms"][0]["devices"][0][
            "reported_target_temperature"
        ] = 17.0
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.sync-stale-axis",
            "expected_state_revision": self.home["state_revision"],
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "synchronize_home",
            "room_id": None,
            "parameters": {},
        }

        receipt = await self.service.async_execute(request)

        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("partial", receipt["status"])
        self.assertEqual("accepted_unverified", leaf["execution_state"])
        self.assertNotIn("evidence", leaf)

    async def test_reliable_scope_excludes_device_without_requested_axis(self) -> None:
        self.runtime.home["rooms"][0]["devices"].append(
            {
                "id": "living_humidifier",
                "name": "Увлажнитель в гостиной",
                "kind": "humidifier",
                "control_scope": "managed",
                "capabilities": ["target_humidity", "power"],
                "available": True,
                "state": "working",
            }
        )
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.target-axis-scope"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            ["living_ac"],
            receipt["action_snapshot"]["resolved_scope"]["device_ids"],
        )
        self.assertEqual(
            {"living_ac"}, set(receipt["outcomes"]["rooms"]["living"]["devices"]),
        )

    async def test_reliable_invalid_single_leaf_map_stays_ambiguous(self) -> None:
        async def invalid_leaf_map(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.PARTIAL,
                command_count=1,
                accepted_count=1,
                confirmed_room_count=0,
                device_outcomes={
                    "living_ac": {
                        "execution_state": "accepted_unverified",
                        "command_count": 0,
                        "accepted_count": 0,
                    }
                },
            )

        self.runtime.async_temporary_temperature = invalid_leaf_map
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.invalid-map"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertEqual("pending_dispatch", leaf["execution_state"])
        self.assertNotIn("command_count", leaf)
        self.assertNotIn("accepted_count", leaf)
        self.assertEqual(
            "started", self.store.payload["records"][0]["dispatch_ledger"]["state"]
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliable_started_recovery_is_persisted_once(self) -> None:
        async def invalid_leaf_map(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.PARTIAL,
                command_count=1,
                accepted_count=1,
                confirmed_room_count=0,
                device_outcomes={
                    "living_ac": {
                        "execution_state": "accepted_unverified",
                        "command_count": 0,
                        "accepted_count": 0,
                    }
                },
            )

        self.runtime.async_temporary_temperature = invalid_leaf_map
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.started-reload"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)

        first = ClimateTabletService(
            FakeRuntime(self.runtime.home), self.store,
            now_ms=lambda: self.now + 100,
        )
        await first.async_load()
        persisted = copy.deepcopy(self.store.payload["records"][0])
        self.assertEqual("terminal_mixed", persisted["dispatch_ledger"]["state"])

        second = ClimateTabletService(
            FakeRuntime(self.runtime.home), self.store,
            now_ms=lambda: self.now + 200,
        )
        await second.async_load()
        self.assertEqual(persisted, self.store.payload["records"][0])

    async def test_reliable_conflicting_zero_aggregate_map_stays_ambiguous(self) -> None:
        async def conflicting_zero_aggregate(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.PARTIAL,
                command_count=0,
                accepted_count=0,
                confirmed_room_count=0,
                device_outcomes={
                    "living_ac": {
                        "execution_state": "accepted_unverified",
                        "command_count": 1,
                        "accepted_count": 1,
                        "retry_policy": "forbidden_after_dispatch",
                    }
                },
            )

        self.runtime.async_temporary_temperature = conflicting_zero_aggregate
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.zero-map"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertEqual(
            "started", self.store.payload["records"][0]["dispatch_ledger"]["state"]
        )
        self.assertEqual(0, len(self.runtime.commands))
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliable_zero_command_with_acceptance_stays_ambiguous(self) -> None:
        async def contradictory_aggregate(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.PARTIAL,
                command_count=0,
                accepted_count=1,
                confirmed_room_count=0,
            )

        self.runtime.async_temporary_temperature = contradictory_aggregate
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.zero-accepted"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertEqual(
            "started", self.store.payload["records"][0]["dispatch_ledger"]["state"]
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliable_boolean_aggregate_counters_stay_ambiguous(self) -> None:
        async def boolean_aggregate(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.PARTIAL,
                command_count=True,
                accepted_count=True,
                confirmed_room_count=0,
            )

        self.runtime.async_temporary_temperature = boolean_aggregate
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.boolean-count"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertEqual(
            "started", self.store.payload["records"][0]["dispatch_ledger"]["state"]
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliable_already_in_sync_zero_call_stays_confirmed(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.already-in-sync"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        parsed = parse_climate_tablet_action(request)
        room = self.runtime.home["rooms"][0]
        device = room["devices"][0]
        device["reported_target_temperature"] = 23.5
        # This is an existing, authoritative state.  It predates the request
        # because no physical command is necessary for already_in_sync.
        device["observed_at"] = self.now - 1

        async def already_in_sync(payload: object, now: object) -> object:
            del payload, now
            evidence = climate_tablet_module._reliable_evidence(
                parsed,
                room,
                device,
                {**self.runtime.home, "fresh": True},
                self.service._last_reliability_metadata[("living", "living_ac")],
            )
            return SimpleNamespace(
                status=ContourApplyStatus.CONFIRMED,
                command_count=0,
                accepted_count=0,
                confirmed_room_count=1,
                device_outcomes={
                    "living_ac": {
                        "status": "confirmed",
                        "reason": "none",
                        "execution_state": "already_in_sync",
                        "message_code": "confirmed",
                        "message": "Устройство уже соответствует сохранённой цели.",
                        "command_count": 0,
                        "accepted_count": 0,
                        "evidence": evidence,
                    }
                },
            )

        self.runtime.async_temporary_temperature = already_in_sync
        receipt = await self.service.async_execute(request)

        self.assertEqual("confirmed", receipt["status"])
        self.assertEqual(
            "already_in_sync",
            receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"][
                "execution_state"
            ],
        )
        self.assertEqual(
            "already_in_sync",
            self.store.payload["records"][0]["dispatch_ledger"]["state"],
        )
        self.assertEqual([], self.runtime.commands)
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload)
        )
        await restored.async_load()
        self.assertEqual(
            "already_in_sync",
            restored._records_by_request[parsed.request_id].dispatch_ledger["state"],
        )

    async def test_reliable_terminal_zero_call_blocked_result_is_not_reopened_as_pending(self) -> None:
        async def terminal_blocked(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.REJECTED,
                command_count=0,
                accepted_count=0,
                confirmed_room_count=0,
                device_outcomes={
                    "living_ac": {
                        "status": "not_attempted",
                        "reason": "configuration_error",
                        "execution_state": "blocked_before_dispatch",
                        "message_code": "configuration_error",
                        "message": "Конфигурация устройства требует проверки.",
                        "command_count": 0,
                        "accepted_count": 0,
                    }
                },
            )

        self.runtime.async_temporary_temperature = terminal_blocked
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.zero-blocked"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        receipt = await self.service.async_execute(request)

        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertTrue(receipt["final"])
        self.assertEqual("partial", receipt["status"])
        self.assertEqual("blocked_before_dispatch", leaf["execution_state"])
        self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
        self.assertEqual("blocked_before_dispatch", self.store.payload["records"][0]["dispatch_ledger"]["state"])
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

        duplicate = await self.service.async_execute(request)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("blocked_before_dispatch", duplicate["outcomes"]["rooms"]["living"]["devices"]["living_ac"]["execution_state"])
        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), self.store
        )
        await restored.async_load()
        self.assertEqual(receipt, await restored.async_operation(receipt["operation_id"]))

    async def test_reliable_already_in_sync_without_evidence_stays_ambiguous(self) -> None:
        async def proofless_already_in_sync(payload: object, now: object) -> object:
            del payload, now
            return SimpleNamespace(
                status=ContourApplyStatus.CONFIRMED,
                command_count=0,
                accepted_count=0,
                confirmed_room_count=1,
                device_outcomes={
                    "living_ac": {
                        "status": "confirmed",
                        "reason": "none",
                        "execution_state": "already_in_sync",
                        "message_code": "confirmed",
                        "message": "Устройство уже соответствует сохранённой цели.",
                        "command_count": 0,
                        "accepted_count": 0,
                        "evidence": {},
                    }
                },
            )

        self.runtime.async_temporary_temperature = proofless_already_in_sync
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.proofless-zero"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertEqual(
            "started", self.store.payload["records"][0]["dispatch_ledger"]["state"]
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliable_already_in_sync_rejects_invalid_source_timestamp(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.invalid-zero-proof"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        parsed = parse_climate_tablet_action(request)
        room = self.runtime.home["rooms"][0]
        device = room["devices"][0]
        device["reported_target_temperature"] = 23.5
        device["observed_at"] = 0

        async def invalid_zero_proof(payload: object, now: object) -> object:
            del payload, now
            evidence = climate_tablet_module._reliable_evidence(
                parsed,
                room,
                device,
                {**self.runtime.home, "fresh": True},
                self.service._last_reliability_metadata[("living", "living_ac")],
            )
            return SimpleNamespace(
                status=ContourApplyStatus.CONFIRMED,
                command_count=0,
                accepted_count=0,
                confirmed_room_count=1,
                device_outcomes={
                    "living_ac": {
                        "status": "confirmed",
                        "reason": "none",
                        "execution_state": "already_in_sync",
                        "message_code": "confirmed",
                        "message": "Устройство уже соответствует сохранённой цели.",
                        "command_count": 0,
                        "accepted_count": 0,
                        "evidence": evidence,
                    }
                },
            )

        self.runtime.async_temporary_temperature = invalid_zero_proof
        receipt = await self.service.async_execute(request)

        self.assertEqual("pending", receipt["status"])
        self.assertFalse(receipt["final"])
        self.assertEqual(
            "started", self.store.payload["records"][0]["dispatch_ledger"]["state"]
        )
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_reliable_pre_call_failure_does_not_forge_acceptance(self) -> None:
        async def fail_before_call(payload: object, now: object) -> object:
            del payload, now
            raise RuntimeError("injected pre-call failure")

        self.runtime.async_temporary_temperature = fail_before_call
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.pre-call-failure"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )

        receipt = await self.service.async_execute(request)
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]

        self.assertEqual([], self.runtime.commands)
        self.assertEqual("partial", receipt["status"])
        self.assertTrue(receipt["final"])
        self.assertEqual("dispatched_not_accepted", leaf["execution_state"])
        self.assertEqual((1, 0), (leaf["command_count"], leaf["accepted_count"]))
        contract_validator("climate-operation-receipt.schema.json").validate(receipt)

    async def test_unknown_device_mode_is_rejected_before_durable_reservation(self) -> None:
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "tablet.climate.unknown-device",
            "expected_state_revision": self.home["state_revision"],
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_device_mode",
            "room_id": "living",
            "parameters": {"device_id": "missing_ac", "mode": "automatic"},
        }

        with self.assertRaises(ClimateTabletViolation):
            await self.service.async_execute(request)

        self.assertEqual(0, (await self.service.async_snapshot())["control_revision"])
        self.assertEqual({}, self.service._records_by_request)
        self.assertEqual({}, self.service._desired_intents)

    async def test_disabled_runtime_rejects_reliable_missing_scope_before_reservation(self) -> None:
        self.runtime.configuration.climate_bridge_mode = ClimateControlMode.DISABLED
        self.runtime.configuration.mode = "disabled"
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.disabled-missing"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
            room_id="missing",
        )

        with self.assertRaises(ClimateTabletViolation) as captured:
            await self.service.async_execute(request)

        self.assertEqual("climate_disabled", captured.exception.code)
        self.assertEqual(0, (await self.service.async_snapshot())["control_revision"])
        self.assertEqual({}, self.service._records_by_request)
        self.assertEqual({}, self.service._desired_intents)

    async def test_desired_intent_loader_rejects_forged_target(self) -> None:
        request = action_request(
            self.home["state_revision"], request_id="tablet.climate.intent-corrupt"
        )
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await self.service.async_execute(request)
        damaged = copy.deepcopy(self.store.payload)
        damaged["desired_intents"]["room:living"]["parameters"][
            "target_temperature"
        ] = 25.0

        restored = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(damaged)
        )

        with self.assertRaises(ClimateTabletUnavailable):
            await restored.async_load()

    async def test_intent_origin_survives_bounded_operation_history(self) -> None:
        operation_number = 0
        limit = 4

        def next_operation_id() -> str:
            nonlocal operation_number
            operation_number += 1
            return f"{operation_number:032x}"

        self.service._operation_id_factory = next_operation_id
        original_limit = climate_tablet_module.MAX_RELIABLE_OPERATION_RECORDS
        climate_tablet_module.MAX_RELIABLE_OPERATION_RECORDS = limit
        try:
            for index in range(limit + 1):
                request = action_request(
                    self.home["state_revision"],
                    request_id=f"tablet.climate.intent-retention-{index}",
                )
                request["correlation_id"] = "corr.tablet.climate.intent-retention"
                await self.service.async_execute(request)
        finally:
            climate_tablet_module.MAX_RELIABLE_OPERATION_RECORDS = original_limit

        self.assertLessEqual(
            len(self.service._records_by_request), limit
        )
        latest_fingerprint = self.service._desired_intents["room:living"][
            "request_fingerprint"
        ]
        latest_request_id = self.service._desired_intents["room:living"][
            "origin_request_id"
        ]
        self.assertIn(
            (latest_request_id, latest_fingerprint),
            {
                (record.request.request_id, record.fingerprint)
                for record in self.service._records_by_request.values()
            },
        )
        restarted = ClimateTabletService(
            FakeRuntime(self.runtime.home), MemoryOperationStore(self.store.payload)
        )
        await restarted.async_load()
        self.assertEqual(
            limit + 1,
            (await restarted.async_snapshot())["control_revision"],
        )


if __name__ == "__main__":
    unittest.main()
