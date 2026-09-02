"""Restart-contract tests for the complete native climate setup storage."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

from custom_components.hausman_hub.application.climate_runtime import ClimateRuntime
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    contour_registry_to_payload,
)
from custom_components.hausman_hub.application.climate_registry import (
    registry_to_payload,
)
from custom_components.hausman_hub.domain.climate import REGISTRY_VERSION
from custom_components.hausman_hub.domain.climate_bridge import ClimateControlMode
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from custom_components.hausman_hub.domain.contours import CONTOUR_REGISTRY_VERSION
from tests.climate_bridge_fixture import import_climate_state
from tests.test_climate_import import source_payload


def _fake_ha_storage_modules() -> tuple[dict[str, types.ModuleType], type]:
    modules: dict[str, types.ModuleType] = {}
    if "homeassistant" not in sys.modules:
        homeassistant = types.ModuleType("homeassistant")
        homeassistant.__path__ = []
        modules["homeassistant"] = homeassistant
    if "homeassistant.core" not in sys.modules:
        core = types.ModuleType("homeassistant.core")
        core.HomeAssistant = object  # type: ignore[attr-defined]
        modules["homeassistant.core"] = core
    if "homeassistant.helpers" not in sys.modules:
        helpers = types.ModuleType("homeassistant.helpers")
        helpers.__path__ = []
        modules["homeassistant.helpers"] = helpers
    storage = types.ModuleType("homeassistant.helpers.storage")

    class FakeStore:
        backing: dict[str, dict[str, object]] = {}

        def __class_getitem__(cls, item: object) -> type:
            del item
            return cls

        def __init__(self, hass: object, version: int, key: str, **kwargs: object) -> None:
            self.hass = hass
            self.version = version
            self.key = key
            self.max_readable_version = kwargs.get("max_readable_version")

        async def async_load(self) -> dict[str, object] | None:
            return self.backing.get(self.key)

        async def async_save(self, value: dict[str, object]) -> None:
            self.backing[self.key] = value

    storage.Store = FakeStore  # type: ignore[attr-defined]
    modules["homeassistant.helpers.storage"] = storage
    return modules, FakeStore


class CompleteClimateStorageRestartTest(unittest.IsolatedAsyncioTestCase):
    """Prove that one saved setup survives construction of a new runtime."""

    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        sys.modules.pop("custom_components.hausman_hub.climate_storage", None)
        sys.modules.pop("custom_components.hausman_hub.contour_storage", None)
        sys.modules.pop("custom_components.hausman_hub.climate_operation_storage", None)
        self.hass = MagicMock()

    def tearDown(self) -> None:
        sys.modules.pop("custom_components.hausman_hub.climate_storage", None)
        sys.modules.pop("custom_components.hausman_hub.contour_storage", None)
        sys.modules.pop("custom_components.hausman_hub.climate_operation_storage", None)
        self.module_patch.stop()

    def _stores(self, entry_id: str):
        from custom_components.hausman_hub.climate_storage import (
            HomeAssistantClimateRegistryStore,
        )
        from custom_components.hausman_hub.contour_storage import (
            HomeAssistantContourStore,
        )

        return (
            HomeAssistantClimateRegistryStore(self.hass, entry_id),
            HomeAssistantContourStore(self.hass, entry_id),
        )

    @staticmethod
    def _configuration() -> SafeConfiguration:
        return SafeConfiguration(
            mode="read-only",
            climate_bridge_mode=ClimateControlMode.DISABLED,
        )

    async def test_complete_setup_survives_new_store_and_runtime_instances(self) -> None:
        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living", "kids"],
            source_ids=[
                "synthetic-ac-source-living",
                "synthetic-humidifier-source-kids",
            ],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        registry_store, contour_store = self._stores("entry_1")
        first_runtime = ClimateRuntime(
            entry_id="entry_1",
            configuration=self._configuration(),
            registry_store=registry_store,
            contour_store=contour_store,
        )
        await first_runtime.async_start()

        await first_runtime.async_replace_contour_setup(
            registry_to_payload(registry),
            contour_registry_to_payload(contours),
        )

        restarted_registry_store, restarted_contour_store = self._stores("entry_1")
        restarted_runtime = ClimateRuntime(
            entry_id="entry_1",
            configuration=self._configuration(),
            registry_store=restarted_registry_store,
            contour_store=restarted_contour_store,
        )
        await restarted_runtime.async_start()

        self.assertIsNone(restarted_runtime.last_error)
        self.assertEqual(len(registry.rooms), restarted_runtime.room_count)
        self.assertEqual(len(registry.devices), restarted_runtime.device_count)
        self.assertEqual(
            registry_to_payload(registry),
            await restarted_runtime.async_registry_payload(),
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            await restarted_runtime.async_contour_registry_payload(),
        )
        self.assertEqual(
            registry_to_payload(registry),
            self.fake_store.backing["hausman_hub.climate_registry.entry_1"],
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            self.fake_store.backing["hausman_hub.contours.entry_1"],
        )

    async def test_clean_install_backup_restore_preserves_stable_public_ids(self) -> None:
        """Exercise the DR sequence without touching a running Home Assistant."""

        snapshot = import_climate_state(source_payload())
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
        registry_store, contour_store = self._stores("entry_dr")
        await registry_store.async_save(registry)
        await contour_store.async_save(contours)
        backup = deepcopy(self.fake_store.backing)
        expected_room_ids = tuple(room.room_id for room in registry.rooms)
        expected_device_ids = tuple(device.device_id for device in registry.devices)

        self.fake_store.backing.clear()
        clean_registry_store, clean_contour_store = self._stores("entry_dr")
        clean_runtime = ClimateRuntime(
            entry_id="entry_dr",
            configuration=self._configuration(),
            registry_store=clean_registry_store,
            contour_store=clean_contour_store,
        )
        await clean_runtime.async_start()
        self.assertEqual(0, clean_runtime.room_count)
        self.assertEqual(0, clean_runtime.device_count)

        self.fake_store.backing.update(deepcopy(backup))
        restored_registry_store, restored_contour_store = self._stores("entry_dr")
        restored_runtime = ClimateRuntime(
            entry_id="entry_dr",
            configuration=self._configuration(),
            registry_store=restored_registry_store,
            contour_store=restored_contour_store,
        )
        await restored_runtime.async_start()
        restored_registry = await restored_registry_store.async_load()

        self.assertIsNone(restored_runtime.last_error)
        self.assertEqual(
            expected_room_ids,
            tuple(room.room_id for room in restored_registry.rooms),
        )
        self.assertEqual(
            expected_device_ids,
            tuple(device.device_id for device in restored_registry.devices),
        )
        self.assertEqual(
            registry_to_payload(registry),
            await restored_runtime.async_registry_payload(),
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            await restored_runtime.async_contour_registry_payload(),
        )

    async def test_native_tablet_reserved_revision_uses_one_operation_store(self) -> None:
        """A tablet reservation is the runtime's exact direct-control token."""

        from dataclasses import replace
        from datetime import datetime
        from custom_components.hausman_hub.application.climate_tablet import ClimateTabletService
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore
        from tests import test_climate_native_runtime as native

        states = native.safe_stop_states()
        ac = states["climate.living_ac"]
        states[ac.entity_id] = replace(
            ac, attributes={**ac.attributes, "temperature": 24.0},
            last_updated_ms=native.NOW - 10_000_000,
        )
        view = native.MutableStateView(states)
        executor = native.ReflectingStrictExecutor(view)
        store = HomeAssistantClimateOperationStore(
            self.hass, "shared-direct", reliable_scope_integrity_key="5" * 64
        )
        runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED, view, executor, direct_control_store=store
        )
        await runtime.async_start()
        tablet = ClimateTabletService(
            runtime, store, now_ms=lambda: native.NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        snapshot = await tablet.async_snapshot()
        receipt = await tablet.async_execute({
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "shared-stale-1", "expected_state_revision": snapshot["state_revision"],
            "action": "set_room_target", "room_id": "living",
            "parameters": {"target_temperature": 24.0},
            "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0,
        })
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertTrue(receipt["final"])
        self.assertEqual("partial", receipt["status"])
        self.assertEqual("saved_blocked_before_dispatch", receipt["intent"]["status"])
        self.assertEqual(1, receipt["resulting_control_revision"])
        self.assertEqual("blocked_before_dispatch", leaf["execution_state"])
        self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
        self.assertEqual([], executor.calls)
        duplicate = await tablet.async_execute({
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "shared-stale-1", "expected_state_revision": snapshot["state_revision"],
            "action": "set_room_target", "room_id": "living", "parameters": {"target_temperature": 24.0},
            "reliability_profile": "climate_reliability_v1", "expected_control_revision": 0,
        })
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual("blocked_before_dispatch", duplicate["outcomes"]["rooms"]["living"]["devices"]["living_ac"]["execution_state"])
        self.assertEqual(receipt, await tablet.async_operation(receipt["operation_id"]))

        fresh = states["climate.living_ac"]
        states[fresh.entity_id] = replace(fresh, last_updated_ms=native.NOW)
        restarted_store = HomeAssistantClimateOperationStore(
            self.hass, "shared-direct", reliable_scope_integrity_key="5" * 64
        )
        restarted_view = native.MutableStateView(states)
        restarted_executor = native.ReflectingStrictExecutor(restarted_view)
        restarted_runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED, restarted_view, restarted_executor,
            direct_control_store=restarted_store,
        )
        await restarted_runtime.async_start()
        restarted_tablet = ClimateTabletService(
            restarted_runtime, restarted_store, now_ms=lambda: native.NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await restarted_tablet.async_load()
        self.assertEqual(receipt, await restarted_tablet.async_operation(receipt["operation_id"]))
        successor_snapshot = await restarted_tablet.async_snapshot()
        successor_request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "shared-fresh-2", "expected_state_revision": successor_snapshot["state_revision"],
            "action": "set_room_target", "room_id": "living", "parameters": {"target_temperature": 24.0},
            "reliability_profile": "climate_reliability_v1", "expected_control_revision": 1,
        }
        successor = await restarted_tablet.async_execute(successor_request)
        successor_leaf = successor["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("confirmed", successor["status"])
        self.assertTrue(successor["final"])
        self.assertEqual("already_in_sync", successor_leaf["execution_state"])
        self.assertEqual((0, 0), (successor_leaf["command_count"], successor_leaf["accepted_count"]))
        self.assertEqual(2, successor["resulting_control_revision"])
        self.assertEqual([], restarted_executor.calls)
        self.assertEqual(2, await restarted_store.async_current_control_revision())
        duplicate_successor = await restarted_tablet.async_execute(successor_request)
        self.assertTrue(duplicate_successor["duplicate"])
        self.assertEqual([], restarted_executor.calls)

        # The native direct-control ledger can outlive a reconstructed tablet
        # ledger.  Its trusted Tablet identity must still replay exactly, with
        # no second physical dispatch or relaxed fingerprint comparison.
        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntimeUnavailable,
        )
        from custom_components.hausman_hub.application.contour_apply import (
            ContourApplyViolation,
        )

        reconstructed_executor = native.ReflectingStrictExecutor(
            native.MutableStateView(states)
        )
        reconstructed_runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED,
            native.MutableStateView(states),
            reconstructed_executor,
            direct_control_store=HomeAssistantClimateOperationStore(
                self.hass, "shared-direct", reliable_scope_integrity_key="5" * 64
            ),
        )
        await reconstructed_runtime.async_start()
        from custom_components.hausman_hub.application.climate_tablet import (
            parse_climate_tablet_action,
        )

        parsed_successor = parse_climate_tablet_action(successor_request)
        replay = await reconstructed_runtime.async_execute_reserved_tablet_action(
            action=parsed_successor.action,
            room_id=parsed_successor.room_id,
            parameters=dict(parsed_successor.parameters),
            request_id=parsed_successor.request_id,
            correlation_id=parsed_successor.correlation_id,
            expected_control_revision=parsed_successor.expected_control_revision,
            resulting_control_revision=2,
            local_now=datetime(2026, 7, 19, 12, 0),
            tablet_request_fingerprint=parsed_successor.fingerprint,
            tablet_action=parsed_successor.action,
            tablet_parameters=dict(parsed_successor.parameters),
        )
        self.assertEqual("confirmed", replay.status.value)
        self.assertEqual((0, 0), (replay.command_count, replay.accepted_count))
        self.assertEqual([], reconstructed_executor.calls)
        with self.assertRaises(ContourApplyViolation):
            await reconstructed_runtime.async_execute_reserved_tablet_action(
                action=parsed_successor.action,
                room_id=parsed_successor.room_id,
                parameters=dict(parsed_successor.parameters),
                request_id=parsed_successor.request_id,
                correlation_id=parsed_successor.correlation_id,
                expected_control_revision=parsed_successor.expected_control_revision,
                resulting_control_revision=2,
                local_now=datetime(2026, 7, 19, 12, 0),
                tablet_request_fingerprint="0" * 64,
                tablet_action=parsed_successor.action,
                tablet_parameters=dict(parsed_successor.parameters),
            )
        with self.assertRaises(ClimateRuntimeUnavailable):
            await reconstructed_runtime.async_execute_reserved_tablet_action(
                action=parsed_successor.action,
                room_id=parsed_successor.room_id,
                parameters=dict(parsed_successor.parameters),
                request_id=parsed_successor.request_id,
                correlation_id=parsed_successor.correlation_id,
                expected_control_revision=parsed_successor.expected_control_revision,
                resulting_control_revision=2,
                local_now=datetime(2026, 7, 19, 12, 0),
                tablet_request_fingerprint=parsed_successor.fingerprint,
                tablet_action="clear_room_override",
                tablet_parameters=dict(parsed_successor.parameters),
            )
        with self.assertRaises(ClimateRuntimeUnavailable):
            await reconstructed_runtime.async_execute_reserved_tablet_action(
                action=parsed_successor.action,
                room_id=parsed_successor.room_id,
                parameters=dict(parsed_successor.parameters),
                request_id=parsed_successor.request_id,
                correlation_id=parsed_successor.correlation_id,
                expected_control_revision=parsed_successor.expected_control_revision,
                resulting_control_revision=2,
                local_now=datetime(2026, 7, 19, 12, 0),
                tablet_request_fingerprint=parsed_successor.fingerprint,
                tablet_action=parsed_successor.action,
                tablet_parameters={"target_temperature": 24.5},
            )

    async def test_native_tablet_reserved_clear_replays_fresh_scheduled_target(self) -> None:
        """A reliable Tablet clear can confirm from a fresh scheduled target."""

        from dataclasses import replace
        from datetime import datetime
        from custom_components.hausman_hub.application.climate_tablet import (
            ClimateTabletService,
            parse_climate_tablet_action,
        )
        from custom_components.hausman_hub.application.contour_apply import (
            ContourApplyViolation,
        )
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )
        from tests import test_climate_native_runtime as native

        states = native.safe_stop_states()
        view = native.MutableStateView(states)
        executor = native.ReflectingStrictExecutor(view)
        store = HomeAssistantClimateOperationStore(
            self.hass, "shared-clear", reliable_scope_integrity_key="6" * 64
        )
        runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED, view, executor, direct_control_store=store
        )
        await runtime.async_start()
        await runtime.async_temporary_temperature(
            {
                "request_id": "shared-clear-setup",
                "contour_id": "climate", "room_id": "living",
                "action": "set", "target_temperature": 23.5, "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        ac = states["climate.living_ac"]
        states[ac.entity_id] = replace(
            ac, attributes={**ac.attributes, "temperature": 24.0},
            last_updated_ms=native.NOW,
        )
        executor.calls.clear()
        tablet = ClimateTabletService(
            runtime, store, now_ms=lambda: native.NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        snapshot = await tablet.async_snapshot()
        request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "shared-clear-1",
            "expected_state_revision": snapshot["state_revision"],
            "expected_control_revision": 0,
            "reliability_profile": "climate_reliability_v1",
            "action": "clear_room_override", "room_id": "living", "parameters": {},
        }
        receipt = await tablet.async_execute(request)
        leaf = receipt["outcomes"]["rooms"]["living"]["devices"]["living_ac"]
        self.assertEqual("confirmed", receipt["status"])
        self.assertTrue(receipt["final"])
        self.assertEqual("already_in_sync", leaf["execution_state"])
        self.assertEqual((0, 0), (leaf["command_count"], leaf["accepted_count"]))
        self.assertEqual(1, receipt["resulting_control_revision"])
        self.assertEqual([], executor.calls)
        self.assertFalse(
            (await runtime.async_contours_snapshot())["contours"][0]["rooms"][0]
            ["temporary_temperature"]["active"]
        )
        duplicate = await tablet.async_execute(request)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(receipt, await tablet.async_operation(receipt["operation_id"]))

        restarted_store = HomeAssistantClimateOperationStore(
            self.hass, "shared-clear", reliable_scope_integrity_key="6" * 64
        )
        restarted_view = native.MutableStateView(states)
        restarted_executor = native.ReflectingStrictExecutor(restarted_view)
        restarted_runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED, restarted_view, restarted_executor,
            direct_control_store=restarted_store,
        )
        await restarted_runtime.async_start()
        restarted_tablet = ClimateTabletService(
            restarted_runtime, restarted_store, now_ms=lambda: native.NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        await restarted_tablet.async_load()
        self.assertEqual(receipt, await restarted_tablet.async_operation(receipt["operation_id"]))
        self.assertTrue((await restarted_tablet.async_execute(request))["duplicate"])
        self.assertEqual([], restarted_executor.calls)

        parsed = parse_climate_tablet_action(request)
        replay = await restarted_runtime.async_execute_reserved_tablet_action(
            action=parsed.action, room_id=parsed.room_id,
            parameters=dict(parsed.parameters), request_id=parsed.request_id,
            correlation_id=parsed.correlation_id,
            expected_control_revision=parsed.expected_control_revision,
            resulting_control_revision=1,
            local_now=datetime(2026, 7, 19, 12, 0),
            tablet_request_fingerprint=parsed.fingerprint,
            tablet_action=parsed.action, tablet_parameters=dict(parsed.parameters),
        )
        self.assertEqual((0, 0), (replay.command_count, replay.accepted_count))
        with self.assertRaises(ContourApplyViolation):
            await restarted_runtime.async_execute_reserved_tablet_action(
                action=parsed.action, room_id=parsed.room_id,
                parameters=dict(parsed.parameters), request_id=parsed.request_id,
                correlation_id=parsed.correlation_id,
                expected_control_revision=parsed.expected_control_revision,
                resulting_control_revision=1,
                local_now=datetime(2026, 7, 19, 12, 0),
                tablet_request_fingerprint="0" * 64,
                tablet_action=parsed.action, tablet_parameters=dict(parsed.parameters),
            )
        self.assertEqual([], restarted_executor.calls)

    async def test_reserved_tablet_action_rejects_unknown_and_malformed_temperature_paths(self) -> None:
        """Trusted reservations remain fail-closed before contour mutation."""

        from datetime import datetime
        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntimeUnavailable,
        )
        from custom_components.hausman_hub.application.climate_tablet import (
            MAX_JS_SAFE_INTEGER,
            ClimateTabletViolation,
            parse_climate_tablet_action,
        )
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )
        from tests import test_climate_native_runtime as native

        states = native.safe_stop_states()
        view = native.MutableStateView(states)
        executor = native.ReflectingStrictExecutor(view)
        store = HomeAssistantClimateOperationStore(
            self.hass, "reserved-adversarial", reliable_scope_integrity_key="7" * 64
        )
        runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED, view, executor, direct_control_store=store
        )
        await runtime.async_start()
        await runtime.async_temporary_temperature(
            {
                "request_id": "reserved-adversarial-setup", "contour_id": "climate",
                "room_id": "living", "action": "set", "target_temperature": 23.5,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        executor.calls.clear()
        self.assertEqual(1, await store.async_reserve_control_revision(0))
        before = await runtime.async_contours_snapshot()

        boundary_request = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "reserved-max-revision", "correlation_id": "reserved-max-revision",
            "expected_state_revision": MAX_JS_SAFE_INTEGER,
            "expected_control_revision": MAX_JS_SAFE_INTEGER,
            "reliability_profile": "climate_reliability_v1",
            "action": "set_room_target", "room_id": "living",
            "parameters": {"target_temperature": 24.0},
        }
        self.assertEqual(
            MAX_JS_SAFE_INTEGER,
            parse_climate_tablet_action(boundary_request).expected_control_revision,
        )
        too_large_revision = MAX_JS_SAFE_INTEGER + 1
        boundary_request["expected_control_revision"] = too_large_revision
        with self.assertRaises(ClimateTabletViolation):
            parse_climate_tablet_action(boundary_request)
        with self.assertRaises(ClimateRuntimeUnavailable):
            await runtime.async_execute_reserved_tablet_action(
                action="set_room_target", room_id="living",
                parameters={"target_temperature": 24.0},
                request_id="reserved-too-large-revision",
                correlation_id="reserved-too-large-revision",
                expected_control_revision=too_large_revision,
                resulting_control_revision=too_large_revision + 1,
                local_now=datetime(2026, 7, 19, 12, 0),
                tablet_request_fingerprint="a" * 64,
                tablet_action="set_room_target",
                tablet_parameters={"target_temperature": 24.0},
            )

        invalid_requests = (
            ("unknown_action", "living", {}, "reserved-adversarial-unknown"),
            ("set_home_targets", None, {}, "reserved-adversarial-home-missing"),
            ("set_home_targets", None, {"target_temperature": True}, "reserved-adversarial-home-bool"),
            ("set_home_targets", None, {"target_temperature": 29.0}, "reserved-adversarial-home-range"),
            ("synchronize_home", None, {"extra": 1}, "reserved-adversarial-sync-extra"),
            ("set_room_target", "living", {}, "reserved-adversarial-set-missing"),
            ("set_room_target", "living", {"target_temperature": float("inf")}, "reserved-adversarial-set-inf"),
            ("set_room_humidity_target", "living", {"target_humidity": True}, "reserved-adversarial-humidity-bool"),
            ("set_room_min_target", "living", {"minimum_temperature": float("nan")}, "reserved-adversarial-minimum-nan"),
            ("set_room_target_strategy", "living", {"target_strategy": "unsafe"}, "reserved-adversarial-strategy"),
            ("turn_room_off", "living", {"extra": 1}, "reserved-adversarial-off-extra"),
            ("clear_room_override", "living", {"target_temperature": 24.0}, "reserved-adversarial-clear-extra"),
            ("set_room_mode", "living", {"mode": True}, "reserved-adversarial-room-mode-bool"),
            ("set_device_mode", "living", {"device_id": "Bad", "mode": "automatic"}, "reserved-adversarial-device-id"),
            ("set_room_target", "Bad", {"target_temperature": 24.0}, "reserved-adversarial-room-id"),
            ("clear_room_override", "living", {}, "invalid correlation"),
        )
        for index, (action, room_id, parameters, correlation_id) in enumerate(invalid_requests):
            with self.assertRaises(ClimateRuntimeUnavailable):
                await runtime.async_execute_reserved_tablet_action(
                    action=action, room_id=room_id, parameters=parameters,
                    request_id=f"reserved-adversarial-{index}",
                    correlation_id=correlation_id,
                    expected_control_revision=0, resulting_control_revision=1,
                    local_now=datetime(2026, 7, 19, 12, 0),
                    tablet_request_fingerprint="a" * 64,
                    tablet_action=action, tablet_parameters=dict(parameters),
                )
        self.assertEqual(before, await runtime.async_contours_snapshot())
        self.assertEqual([], executor.calls)

    async def test_control_revision_exhaustion_is_atomic_across_store_runtime_and_tablet(self) -> None:
        """The final JS-safe revision is readable but cannot be incremented."""

        from datetime import datetime
        from custom_components.hausman_hub.application.climate_runtime import (
            ClimateRuntimeUnavailable,
        )
        from custom_components.hausman_hub.application.climate_tablet import (
            ClimateTabletService,
            ClimateTabletViolation,
        )
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )
        from custom_components.hausman_hub.climate_revision import MAX_JS_SAFE_INTEGER
        from tests import test_climate_native_runtime as native

        async def seed(store: HomeAssistantClimateOperationStore, revision: int) -> None:
            await store.async_save({
                "version": 2, "records": [], "recoveries": [],
                "desired_intents": {}, "direct_control_records": [],
                "control_revision": revision,
            })

        final_store = HomeAssistantClimateOperationStore(
            self.hass, "revision-final", reliable_scope_integrity_key="8" * 64
        )
        await seed(final_store, MAX_JS_SAFE_INTEGER)
        final_before = await final_store.async_load()
        with self.assertRaises(ValueError):
            await final_store.async_reserve_control_revision(MAX_JS_SAFE_INTEGER)
        self.assertEqual(final_before, await final_store.async_load())
        self.assertEqual(MAX_JS_SAFE_INTEGER, await final_store.async_current_control_revision())

        penultimate_store = HomeAssistantClimateOperationStore(
            self.hass, "revision-penultimate", reliable_scope_integrity_key="9" * 64
        )
        await seed(penultimate_store, MAX_JS_SAFE_INTEGER - 1)
        self.assertEqual(
            MAX_JS_SAFE_INTEGER,
            await penultimate_store.async_reserve_control_revision(MAX_JS_SAFE_INTEGER - 1),
        )
        with self.assertRaises(ValueError):
            await penultimate_store.async_reserve_control_revision(MAX_JS_SAFE_INTEGER)

        states = native.safe_stop_states()
        view = native.MutableStateView(states)
        executor = native.ReflectingStrictExecutor(view)
        runtime = native.native_application_runtime(
            ClimateControlMode.MANAGED, view, executor,
            direct_control_store=final_store,
        )
        await runtime.async_start()
        before_contours = await runtime.async_contours_snapshot()
        with self.assertRaises(ClimateRuntimeUnavailable):
            await runtime.async_execute_reserved_tablet_action(
                action="set_room_target", room_id="living",
                parameters={"target_temperature": 24.0}, request_id="revision-final-direct",
                correlation_id="revision-final-direct",
                expected_control_revision=MAX_JS_SAFE_INTEGER,
                resulting_control_revision=MAX_JS_SAFE_INTEGER + 1,
                local_now=datetime(2026, 7, 19, 12, 0),
                tablet_request_fingerprint="a" * 64,
                tablet_action="set_room_target",
                tablet_parameters={"target_temperature": 24.0},
            )
        tablet = ClimateTabletService(
            runtime, final_store, now_ms=lambda: native.NOW,
            local_now=lambda: datetime(2026, 7, 19, 12, 0),
        )
        snapshot = await tablet.async_snapshot()
        with self.assertRaises(ClimateTabletViolation):
            await tablet.async_execute({
                "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                "request_id": "revision-final-tablet",
                "expected_state_revision": snapshot["state_revision"],
                "expected_control_revision": MAX_JS_SAFE_INTEGER,
                "reliability_profile": "climate_reliability_v1",
                "action": "set_room_target", "room_id": "living",
                "parameters": {"target_temperature": 24.0},
            })
        with self.assertRaises(ClimateTabletViolation) as legacy_error:
            await tablet.async_execute({
                "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
                "request_id": "revision-final-legacy",
                "expected_state_revision": snapshot["state_revision"],
                "action": "set_room_target", "room_id": "living",
                "parameters": {"target_temperature": 24.0},
            })
        self.assertEqual("revision_conflict", legacy_error.exception.code)
        from custom_components.hausman_hub.application.climate_tablet import (
            _StoredOperation,
            parse_climate_tablet_action,
        )
        legacy_payload = {
            "contract": {"name": "hausman-hub-climate-action-request", "version": 1},
            "request_id": "revision-final-legacy-duplicate",
            "expected_state_revision": snapshot["state_revision"],
            "action": "set_room_target", "room_id": "living",
            "parameters": {"target_temperature": 24.0},
        }
        legacy_request = parse_climate_tablet_action(legacy_payload)
        tablet._records_by_request[legacy_request.request_id] = _StoredOperation(
            legacy_request.fingerprint, legacy_request,
            {"request_id": legacy_request.request_id, "accepted": True},
        )
        self.assertTrue((await tablet.async_execute(legacy_payload))["duplicate"])
        self.assertEqual(before_contours, await runtime.async_contours_snapshot())
        self.assertEqual([], executor.calls)
        self.assertEqual(MAX_JS_SAFE_INTEGER, await final_store.async_current_control_revision())

    async def test_tablet_operation_ledger_survives_store_reconstruction(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )

        payload = {
            "version": 1,
            "records": [
                {
                    "request_id": "tablet.climate.0001",
                    "fingerprint": "a" * 64,
                    "receipt": {"operation_id": "b" * 32},
                }
            ],
        }
        first = HomeAssistantClimateOperationStore(self.hass, "entry_1")
        await first.async_save(payload)

        restarted = HomeAssistantClimateOperationStore(self.hass, "entry_1")

        self.assertEqual(payload, await restarted.async_load())
        self.assertEqual(1, restarted._store.version)
        self.assertEqual(
            payload,
            self.fake_store.backing["hausman_hub.climate_operations.entry_1"],
        )

    async def test_operation_store_signs_direct_records_and_shared_revision(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )

        payload = {
            "version": 6,
            "records": [],
            "recoveries": [],
            "control_revision": 3,
            "desired_intents": {},
            "recovery_preflights": [],
            "direct_control_records": [{"request_id": "hacs.climate.1"}],
        }
        store = HomeAssistantClimateOperationStore(
            self.hass, "entry_signed", reliable_scope_integrity_key="1" * 64
        )
        await store.async_save(payload)
        raw = self.fake_store.backing[
            "hausman_hub.climate_operations.entry_signed"
        ]
        self.assertRegex(raw["storage_integrity_tag"], r"^[a-f0-9]{64}$")
        self.assertEqual(payload, await store.async_load())

        raw["control_revision"] = 2
        with self.assertRaisesRegex(ValueError, "integrity"):
            await store.async_load()

        raw["version"] = 5
        raw.pop("storage_integrity_tag", None)
        with self.assertRaisesRegex(ValueError, "integrity"):
            await store.async_load()

    async def test_unsigned_operation_store_migrates_once_then_closes_downgrade(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )

        key = "2" * 64
        storage_key = "hausman_hub.climate_operations.entry_migration"
        self.fake_store.backing[storage_key] = {
            "version": 2, "records": [], "recoveries": [],
            "control_revision": 1, "desired_intents": {},
        }
        migrating = HomeAssistantClimateOperationStore(
            self.hass, "entry_migration",
            reliable_scope_integrity_key=key,
            allow_unsigned_migration=True,
        )
        await migrating.async_migrate_integrity()
        self.assertIn("storage_integrity_tag", self.fake_store.backing[storage_key])
        resumed_migration = HomeAssistantClimateOperationStore(
            self.hass, "entry_migration",
            reliable_scope_integrity_key=key,
            allow_unsigned_migration=True,
        )
        await resumed_migration.async_migrate_integrity()
        strict = HomeAssistantClimateOperationStore(
            self.hass, "entry_migration", reliable_scope_integrity_key=key
        )
        self.assertEqual(1, (await strict.async_load())["control_revision"])

    async def test_tablet_then_hacs_direct_write_preserves_v6_and_revision(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )

        store = HomeAssistantClimateOperationStore(
            self.hass, "entry_shared", reliable_scope_integrity_key="3" * 64
        )
        await store.async_save({
            "version": 6, "records": [], "recoveries": [],
            "control_revision": 1, "desired_intents": {},
            "recovery_preflights": [],
        })
        old_signed_main = deepcopy(
            self.fake_store.backing["hausman_hub.climate_operations.entry_shared"]
        )
        self.assertEqual(2, await store.async_reserve_control_revision(1))
        await store.async_save_direct_control([{"request_id": "hacs.lower.21-5"}])

        restarted = HomeAssistantClimateOperationStore(
            self.hass, "entry_shared", reliable_scope_integrity_key="3" * 64
        )
        payload = await restarted.async_load()
        self.assertEqual(6, payload["version"])
        self.assertEqual(2, payload["control_revision"])
        self.assertEqual(
            [{"request_id": "hacs.lower.21-5"}],
            await restarted.async_load_direct_control(),
        )
        self.fake_store.backing[
            "hausman_hub.climate_operations.entry_shared"
        ] = old_signed_main
        with self.assertRaisesRegex(ValueError, "generation"):
            await restarted.async_load()

    async def test_tablet_service_reloads_after_hacs_direct_revision(self) -> None:
        from custom_components.hausman_hub.application.climate_tablet import (
            ClimateTabletService,
        )
        from custom_components.hausman_hub.climate_operation_storage import (
            HomeAssistantClimateOperationStore,
        )
        from tests.test_climate_tablet import FakeRuntime, action_request, managed_home

        key = "4" * 64
        store = HomeAssistantClimateOperationStore(
            self.hass, "entry_cross_writer", reliable_scope_integrity_key=key
        )
        runtime = FakeRuntime(managed_home())
        runtime.home["rooms"][0]["devices"][0]["observed_at"] = 1_785_949_319_999
        service = ClimateTabletService(
            runtime, store,
            operation_id_factory=lambda: "4" * 32,
            now_ms=lambda: 1_785_949_320_000,
        )
        request = action_request(runtime.home["state_revision"], target=25.0)
        request.update(
            reliability_profile="climate_reliability_v1",
            expected_control_revision=0,
        )
        await service.async_execute(request)
        first_restart = ClimateTabletService(FakeRuntime(runtime.home), store)
        await first_restart.async_load()

        self.assertEqual(2, await store.async_reserve_control_revision(1))
        await store.async_save_direct_control([{"request_id": "hacs.lower.21-5"}])
        second_restart = ClimateTabletService(FakeRuntime(runtime.home), store)
        await second_restart.async_load()
        self.assertEqual(2, (await second_restart.async_snapshot())["control_revision"])

    async def test_storage_is_isolated_by_entry_and_keeps_schema_versions(self) -> None:
        registry_store, contour_store = self._stores("entry_1")
        other_registry_store, other_contour_store = self._stores("entry_2")
        snapshot = import_climate_state(source_payload())
        registry, contours = build_climate_contour_setup(
            snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )

        await registry_store.async_save(registry)
        await contour_store.async_save(contours)

        self.assertEqual(REGISTRY_VERSION, registry_store._store.version)
        self.assertEqual(CONTOUR_REGISTRY_VERSION, contour_store._store.version)
        self.assertEqual(REGISTRY_VERSION, registry_store._store.max_readable_version)
        self.assertEqual(
            CONTOUR_REGISTRY_VERSION,
            contour_store._store.max_readable_version,
        )
        self.assertEqual(
            registry_to_payload(registry),
            registry_to_payload(await registry_store.async_load()),
        )
        self.assertEqual(
            contour_registry_to_payload(contours),
            contour_registry_to_payload(await contour_store.async_load()),
        )
        self.assertEqual(0, len((await other_registry_store.async_load()).rooms))
        self.assertEqual(0, len((await other_contour_store.async_load()).contours))

class ClimateLedgerKeyringTest(unittest.IsolatedAsyncioTestCase):
    """The external keyring is the only production signing authority."""

    def setUp(self) -> None:
        self.modules, self.fake_store = _fake_ha_storage_modules()
        self.fake_store.backing.clear()
        self.module_patch = patch.dict(sys.modules, self.modules)
        self.module_patch.start()
        sys.modules.pop("custom_components.hausman_hub.climate_operation_storage", None)
        self.hass = MagicMock()
        self.keyring_directory = tempfile.TemporaryDirectory()
        self.keyring_paths: dict[str, str] = {}

    def tearDown(self) -> None:
        sys.modules.pop("custom_components.hausman_hub.climate_operation_storage", None)
        self.module_patch.stop()
        self.keyring_directory.cleanup()

    def _external_migration_keyring(self, entry_id: str, legacy_key: str = ""):
        from custom_components.hausman_hub.climate_ledger_keyring import (
            KEYRING_PATH_ENV,
            load_external_climate_ledger_keyring,
        )

        path = f"{self.keyring_directory.name}/{entry_id}.json"
        self.keyring_paths[entry_id] = path
        del legacy_key
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"active_key_id": "external", "keys": {"external": "a" * 64}}, stream)
        os.chmod(path, 0o600)
        return load_external_climate_ledger_keyring(
            environ={KEYRING_PATH_ENV: path}
        )

    def _reload_keyring(self, entry_id: str):
        from custom_components.hausman_hub.climate_ledger_keyring import (
            KEYRING_PATH_ENV,
            load_external_climate_ledger_keyring,
        )

        return load_external_climate_ledger_keyring(
            environ={KEYRING_PATH_ENV: self.keyring_paths[entry_id]}
        )

    def test_external_keyring_rejects_home_assistant_config_path(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import (
            ClimateLedgerKeyringError,
            KEYRING_PATH_ENV,
            load_external_climate_ledger_keyring,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/keyring.json"
            with open(path, "w", encoding="utf-8") as stream:
                json.dump({"active_key_id": "k1", "keys": {"k1": "a" * 64}}, stream)
            with self.assertRaises(ClimateLedgerKeyringError):
                load_external_climate_ledger_keyring(
                    config_dir=directory, environ={KEYRING_PATH_ENV: path}
                )

    def test_external_keyring_rejects_group_readable_file(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import (
            ClimateLedgerKeyringError,
            KEYRING_PATH_ENV,
            load_external_climate_ledger_keyring,
        )

        path = f"{self.keyring_directory.name}/readable-keyring.json"
        with open(path, "w", encoding="utf-8") as stream:
            json.dump({"active_key_id": "k1", "keys": {"k1": "a" * 64}}, stream)
        os.chmod(path, 0o640)
        with self.assertRaises(ClimateLedgerKeyringError):
            load_external_climate_ledger_keyring(environ={KEYRING_PATH_ENV: path})

    def test_external_keyring_rejects_symlink(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import (
            ClimateLedgerKeyringError,
            KEYRING_PATH_ENV,
            load_external_climate_ledger_keyring,
        )

        target = f"{self.keyring_directory.name}/target-keyring.json"
        link = f"{self.keyring_directory.name}/keyring-link.json"
        with open(target, "w", encoding="utf-8") as stream:
            json.dump({"active_key_id": "k1", "keys": {"k1": "a" * 64}}, stream)
        os.chmod(target, 0o600)
        os.symlink(target, link)
        with self.assertRaises(ClimateLedgerKeyringError):
            load_external_climate_ledger_keyring(environ={KEYRING_PATH_ENV: link})

    def test_home_assistant_os_creates_private_default_keyring(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import (
            load_external_climate_ledger_keyring,
        )

        path = Path(self.keyring_directory.name) / "ssl" / "hausman_hub" / "climate-ledger.json"
        path.parent.mkdir(mode=0o700, parents=True)
        keyring = load_external_climate_ledger_keyring(
            environ={"HASSIO": "homeassistant"}, ha_os_keyring_path=path,
        )

        self.assertEqual(path, keyring.source_path)
        self.assertEqual("haos-1", keyring.active_key_id)
        self.assertEqual(32, len(keyring.active_key))
        self.assertEqual(0o700, path.parent.stat().st_mode & 0o777)
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.assertEqual(
            keyring.active_key,
            load_external_climate_ledger_keyring(
                environ={"HASSIO": "homeassistant"}, ha_os_keyring_path=path,
            ).active_key,
        )

    def test_home_assistant_os_refuses_an_unsafe_default_directory(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import (
            ClimateLedgerKeyringError,
            load_external_climate_ledger_keyring,
        )

        directory = Path(self.keyring_directory.name) / "unsafe"
        directory.mkdir(mode=0o755)
        with self.assertRaisesRegex(ClimateLedgerKeyringError, "permissions are unsafe"):
            load_external_climate_ledger_keyring(
                environ={"HASSIO": "homeassistant"},
                ha_os_keyring_path=directory / "climate-ledger.json",
            )

    def test_non_haos_without_explicit_provider_remains_fail_closed(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import (
            ClimateLedgerKeyringError,
            load_external_climate_ledger_keyring,
        )

        with self.assertRaisesRegex(ClimateLedgerKeyringError, "not configured"):
            load_external_climate_ledger_keyring(environ={})

    async def test_key_rotation_reads_old_envelope_then_re_signs_with_active_key(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import ClimateLedgerKeyring
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        old = ClimateLedgerKeyring("old", {"old": bytes.fromhex("a" * 64)})
        payload = {"version": 2, "records": [], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        await HomeAssistantClimateOperationStore(self.hass, "rotation", reliable_scope_integrity_key=old).async_save(payload)
        rotated = ClimateLedgerKeyring("new", {"old": bytes.fromhex("a" * 64), "new": bytes.fromhex("b" * 64)})
        store = HomeAssistantClimateOperationStore(self.hass, "rotation", reliable_scope_integrity_key=rotated)
        self.assertEqual(payload, await store.async_load())
        await store.async_save(payload)
        raw = self.fake_store.backing["hausman_hub.climate_operations.rotation"]
        self.assertEqual("hausman_climate_ledger_auth_v1", raw["format"])
        self.assertEqual("new", raw["key_id"])
        self.assertTrue(await store.async_direct_control_is_authenticated())

    async def test_missing_external_keyring_refuses_durable_write(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        store = HomeAssistantClimateOperationStore(
            self.hass, "missing-keyring", require_authenticated=True
        )
        with self.assertRaisesRegex(ValueError, "external climate ledger keyring"):
            await store.async_save({"version": 2, "records": [], "recoveries": [], "control_revision": 0, "desired_intents": {}})

    async def test_external_ledger_readiness_closes_after_every_write_path_failure(self) -> None:
        """A verified anchor cannot remain advertised after any failed write."""
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        for write_path in ("save", "scope", "reserve", "direct"):
            entry_id = f"ready-failure-{write_path}"
            store = HomeAssistantClimateOperationStore(
                self.hass, entry_id,
                reliable_scope_integrity_key=self._external_migration_keyring(entry_id),
                require_authenticated=True,
            )
            await store.async_initialize_external_ledger()
            self.assertTrue(store.authenticated_external_ledger_ready)

            async def fail(_: object) -> None:
                raise OSError(f"{write_path} persistence failure")

            if write_path in {"save", "reserve", "direct"}:
                store._store.async_save = fail
            else:
                store._reliable_scope_store.async_save = fail
            with self.subTest(write_path=write_path), self.assertRaisesRegex(OSError, "persistence failure"):
                if write_path == "save":
                    await store.async_save({"version": 2, "records": [], "recoveries": [], "control_revision": 0, "desired_intents": {}})
                elif write_path == "scope":
                    await store.async_save_reliable_scope_bindings({})
                elif write_path == "reserve":
                    await store.async_reserve_control_revision(0)
                else:
                    await store.async_save_direct_control([])
            self.assertFalse(store.authenticated_external_ledger_ready)

    async def test_stale_reservation_is_a_cas_conflict_not_a_sticky_storage_failure(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import (
            ClimateOperationRevisionConflict,
            HomeAssistantClimateOperationStore,
        )

        entry_id = "ready-stale-reservation"
        store = HomeAssistantClimateOperationStore(
            self.hass, entry_id,
            reliable_scope_integrity_key=self._external_migration_keyring(entry_id),
            require_authenticated=True,
        )
        await store.async_initialize_external_ledger()
        self.assertTrue(store.authenticated_external_ledger_ready)
        self.assertEqual(1, await store.async_reserve_control_revision(0))

        with self.assertRaises(ClimateOperationRevisionConflict):
            await store.async_reserve_control_revision(0)
        self.assertTrue(store.authenticated_external_ledger_ready)

    async def test_envelope_rollback_is_rejected_against_current_sidecar_checkpoint(self) -> None:
        from custom_components.hausman_hub.climate_ledger_keyring import ClimateLedgerKeyring
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        keyring = ClimateLedgerKeyring("k1", {"k1": bytes.fromhex("c" * 64)})
        store = HomeAssistantClimateOperationStore(
            self.hass, "envelope-rollback", reliable_scope_integrity_key=keyring
        )
        first = {"version": 2, "records": [], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        await store.async_save(first)
        old_main = deepcopy(self.fake_store.backing["hausman_hub.climate_operations.envelope-rollback"])
        second = {**first, "control_revision": 2}
        await store.async_save(second)
        self.fake_store.backing["hausman_hub.climate_operations.envelope-rollback"] = old_main

        with self.assertRaisesRegex(ValueError, "generation"):
            await HomeAssistantClimateOperationStore(
                self.hass, "envelope-rollback", reliable_scope_integrity_key=keyring
            ).async_load()

    async def test_external_initialization_discards_legacy_flat_main_and_sidecar(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        legacy_key = "d" * 64
        payload = {"version": 2, "records": [{"forged": True}], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        await HomeAssistantClimateOperationStore(
            self.hass, "legacy-envelope", reliable_scope_integrity_key=legacy_key
        ).async_save(payload)
        keyring = self._external_migration_keyring("legacy-envelope", legacy_key)
        store = HomeAssistantClimateOperationStore(
            self.hass, "legacy-envelope", reliable_scope_integrity_key=keyring,
            require_authenticated=True,
        )
        self.assertTrue(await store.async_initialize_external_ledger())
        raw = self.fake_store.backing["hausman_hub.climate_operations.legacy-envelope"]
        self.assertEqual("hausman_climate_ledger_auth_v1", raw["format"])
        self.assertEqual([], raw["payload"]["records"])
        sidecar_payload = self.fake_store.backing["hausman_hub.climate_operation_scopes.legacy-envelope"]["payload"]
        self.assertEqual({"__storage_state__"}, set(sidecar_payload))
        self.assertEqual(0, (await HomeAssistantClimateOperationStore(
            self.hass, "legacy-envelope", reliable_scope_integrity_key=self._reload_keyring("legacy-envelope")
        ).async_load())["control_revision"])

    async def test_external_initialization_discards_nested_tablet_history(self) -> None:
        from custom_components.hausman_hub.application.climate_tablet import ClimateTabletService
        from custom_components.hausman_hub.climate_ledger_keyring import ClimateLedgerKeyring
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore
        from tests.test_climate_tablet import FakeRuntime, action_request, managed_home

        legacy_key = "f" * 64
        legacy_store = HomeAssistantClimateOperationStore(
            self.hass, "nested-migration", reliable_scope_integrity_key=legacy_key
        )
        runtime = FakeRuntime(managed_home())
        runtime.home["rooms"][0]["devices"][0]["observed_at"] = 1_785_949_319_999
        tablet = ClimateTabletService(
            runtime, legacy_store, operation_id_factory=lambda: "f" * 32,
            now_ms=lambda: 1_785_949_320_000,
        )
        request = action_request(runtime.home["state_revision"], target=25.0)
        request.update(reliability_profile="climate_reliability_v1", expected_control_revision=0)
        await tablet.async_execute(request)

        keyring = self._external_migration_keyring("nested-migration", legacy_key)
        migrating = HomeAssistantClimateOperationStore(
            self.hass, "nested-migration", reliable_scope_integrity_key=keyring,
            require_authenticated=True,
        )
        self.assertTrue(await migrating.async_initialize_external_ledger())
        strict_store = HomeAssistantClimateOperationStore(
            self.hass, "nested-migration",
            reliable_scope_integrity_key=self._reload_keyring("nested-migration"),
            require_authenticated=True,
        )
        restarted = ClimateTabletService(FakeRuntime(runtime.home), strict_store)
        await restarted.async_load()
        self.assertEqual(0, (await restarted.async_snapshot())["control_revision"])

    async def test_external_anchor_rejects_forged_flat_legacy_after_reset(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        legacy_key = "b" * 64
        entry_id = "adversarial-legacy"
        forged_payload = {"version": 2, "records": [], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        legacy_store = HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key=legacy_key
        )
        await legacy_store.async_save(forged_payload)
        forged_main = deepcopy(self.fake_store.backing[f"hausman_hub.climate_operations.{entry_id}"])
        forged_sidecar = deepcopy(self.fake_store.backing[f"hausman_hub.climate_operation_scopes.{entry_id}"])
        keyring = self._external_migration_keyring(entry_id, legacy_key)
        migrating = HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key=keyring,
            require_authenticated=True,
        )
        await migrating.async_initialize_external_ledger()

        self.fake_store.backing[f"hausman_hub.climate_operations.{entry_id}"] = forged_main
        self.fake_store.backing[f"hausman_hub.climate_operation_scopes.{entry_id}"] = forged_sidecar
        with self.assertRaisesRegex(ValueError, "integrity|authentication"):
            await HomeAssistantClimateOperationStore(
                self.hass, entry_id,
                reliable_scope_integrity_key=self._reload_keyring(entry_id),
            ).async_load()

    async def test_initialization_creates_empty_external_anchor_before_later_forgery(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        legacy_key = "c" * 64
        entry_id = "empty-first-migration"
        legacy_store = HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key=legacy_key
        )
        await legacy_store.async_save({
            "version": 2, "records": [], "recoveries": [],
            "control_revision": 1, "desired_intents": {},
        })
        forged_main = deepcopy(self.fake_store.backing[f"hausman_hub.climate_operations.{entry_id}"])
        forged_sidecar = deepcopy(self.fake_store.backing[f"hausman_hub.climate_operation_scopes.{entry_id}"])
        keyring = self._external_migration_keyring(entry_id, legacy_key)
        await HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key=keyring,
            require_authenticated=True,
        ).async_initialize_external_ledger()
        self.assertTrue(self._reload_keyring(entry_id).has_ledger_anchor(entry_id))
        self.assertEqual(0o600, os.stat(self.keyring_paths[entry_id]).st_mode & 0o777)

        # A reset local ConfigEntry marker and an injected legacy ledger must
        # not replace externally anchored history.
        self.fake_store.backing[f"hausman_hub.climate_operations.{entry_id}"] = forged_main
        self.fake_store.backing[f"hausman_hub.climate_operation_scopes.{entry_id}"] = forged_sidecar
        with self.assertRaisesRegex(ValueError, "integrity|authentication"):
            await HomeAssistantClimateOperationStore(
                self.hass, entry_id,
                reliable_scope_integrity_key=self._reload_keyring(entry_id),
            ).async_load()

    async def test_external_anchor_rejects_coordinated_main_and_sidecar_rollback(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        entry_id = "anchor-rollback"
        store = HomeAssistantClimateOperationStore(
            self.hass, entry_id,
            reliable_scope_integrity_key=self._external_migration_keyring(entry_id, "d" * 64),
        )
        first = {"version": 2, "records": [], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        await store.async_save(first)
        old_main = deepcopy(self.fake_store.backing[f"hausman_hub.climate_operations.{entry_id}"])
        old_sidecar = deepcopy(self.fake_store.backing[f"hausman_hub.climate_operation_scopes.{entry_id}"])
        await store.async_save({**first, "control_revision": 2})
        self.fake_store.backing[f"hausman_hub.climate_operations.{entry_id}"] = old_main
        self.fake_store.backing[f"hausman_hub.climate_operation_scopes.{entry_id}"] = old_sidecar
        with self.assertRaisesRegex(ValueError, "anchor"):
            await HomeAssistantClimateOperationStore(
                self.hass, entry_id, reliable_scope_integrity_key=self._reload_keyring(entry_id)
            ).async_load()

    async def test_initial_reset_never_replaces_an_incomplete_pending_anchor(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        entry_id = "retry-first-reset"
        legacy = HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key="d" * 64
        )
        await legacy.async_save({
            "version": 6,
            "records": [{"forged": True}],
            "recoveries": [{"forged": True}],
            "control_revision": 9,
            "desired_intents": {},
            "direct_control_records": [{"forged": True}],
        })
        keyring = self._external_migration_keyring(entry_id)
        first = HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key=keyring,
            require_authenticated=True,
        )

        async def fail_main(_: object) -> None:
            raise OSError("synthetic main write failure")

        first._store.async_save = fail_main
        with self.assertRaisesRegex(OSError, "main write"):
            await first.async_initialize_external_ledger()
        self.assertTrue(self._reload_keyring(entry_id).has_ledger_anchor(entry_id))
        self.assertFalse(self._reload_keyring(entry_id).has_committed_ledger_anchor(entry_id))

        resumed = HomeAssistantClimateOperationStore(
            self.hass, entry_id,
            reliable_scope_integrity_key=self._reload_keyring(entry_id),
            require_authenticated=True,
        )
        with self.assertRaisesRegex(ValueError, "pending climate ledger anchor"):
            await resumed.async_initialize_external_ledger()

    async def test_pending_anchor_recovers_each_local_save_boundary(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        payload = {"version": 2, "records": [], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        for boundary in ("sidecar_first", "main", "sidecar_final"):
            entry_id = f"pending-{boundary}"
            store = HomeAssistantClimateOperationStore(
                self.hass, entry_id,
                reliable_scope_integrity_key=self._external_migration_keyring(entry_id, "e" * 64),
            )
            original_sidecar = store._reliable_scope_store.async_save
            calls = 0

            async def fail_main(value):
                raise OSError("main write failed")

            async def fail_sidecar(value):
                nonlocal calls
                calls += 1
                if (boundary == "sidecar_first" and calls == 1) or (boundary == "sidecar_final" and calls == 2):
                    raise OSError("sidecar write failed")
                await original_sidecar(value)

            if boundary == "main":
                store._store.async_save = fail_main
            else:
                store._reliable_scope_store.async_save = fail_sidecar
            with self.assertRaises(OSError):
                await store.async_save(payload)

            restarted = HomeAssistantClimateOperationStore(
                self.hass, entry_id, reliable_scope_integrity_key=self._reload_keyring(entry_id)
            )
            if boundary == "sidecar_final":
                self.assertEqual(payload, await restarted.async_load())
            else:
                self.assertIsNone(await restarted.async_load())
                await restarted.async_save(payload)

    async def test_final_sidecar_failure_after_existing_revision_promotes_pending_generation(self) -> None:
        from custom_components.hausman_hub.climate_operation_storage import HomeAssistantClimateOperationStore

        entry_id = "final-sidecar-existing-revision"
        store = HomeAssistantClimateOperationStore(
            self.hass, entry_id,
            reliable_scope_integrity_key=self._external_migration_keyring(entry_id, "f" * 64),
        )
        first = {"version": 2, "records": [], "recoveries": [], "control_revision": 1, "desired_intents": {}}
        await store.async_save(first)
        original_sidecar = store._reliable_scope_store.async_save
        calls = 0

        async def fail_final_sidecar(value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("final sidecar write failed")
            await original_sidecar(value)

        store._reliable_scope_store.async_save = fail_final_sidecar
        second = {**first, "control_revision": 2}
        with self.assertRaises(OSError):
            await store.async_save(second)

        restarted = HomeAssistantClimateOperationStore(
            self.hass, entry_id, reliable_scope_integrity_key=self._reload_keyring(entry_id)
        )
        self.assertEqual(second, await restarted.async_load())


if __name__ == "__main__":
    unittest.main()
