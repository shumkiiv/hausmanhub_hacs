"""Pure orchestration tests with in-memory climate and storage adapters."""

from __future__ import annotations

from dataclasses import replace
import json
from datetime import datetime
import unittest

from custom_components.hausman_hub.application.climate_commands import (
    ClimateCommandRejected,
    ClimateCommandViolation,
)
from custom_components.hausman_hub.application.climate_import import import_climate_state
from custom_components.hausman_hub.application.climate_evidence import (
    ClimateShadowEvidence,
)
from custom_components.hausman_hub.application.climate_registry import registry_from_payload
from custom_components.hausman_hub.application.contour_apply import ContourApplyViolation
from custom_components.hausman_hub.application.climate_runtime import (
    ClimateRuntime,
    ClimateRuntimeUnavailable,
)
from custom_components.hausman_hub.application.contours import (
    build_climate_contour_setup,
    contour_registry_to_payload,
    with_active_climate_profile,
    with_applied_climate_schedule_profile,
    with_climate_room_profiles,
    with_climate_schedule,
)
from custom_components.hausman_hub.application.climate_registry import registry_to_payload
from custom_components.hausman_hub.domain.climate import ClimateRegistry
from custom_components.hausman_hub.domain.climate_bridge import (
    ClimateBridgeMode,
    climate_bridge_target,
)
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDataStatus,
)
from custom_components.hausman_hub.domain.climate_resolution import (
    ClimateThermalResolution,
)
from custom_components.hausman_hub.domain.climate_equipment import (
    ClimateEquipmentAction,
)
from custom_components.hausman_hub.domain.climate_isolation import (
    ClimateRoomIsolationStatus,
)
from custom_components.hausman_hub.domain.climate_stability import (
    ClimateStabilityAction,
)
from custom_components.hausman_hub.domain.climate_policy import (
    ClimateFinalDeviceAction,
    ClimateRoomPolicy,
)
from custom_components.hausman_hub.domain.climate_protection import (
    ClimateProtectionMemory,
)
from custom_components.hausman_hub.domain.climate_comparison import (
    ClimateComparisonReason,
    ClimateComparisonStatus,
)
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaCallLimit,
)
from custom_components.hausman_hub.domain.configuration import SafeConfiguration
from custom_components.hausman_hub.domain.native_climate import native_climate_policy
from custom_components.hausman_hub.domain.contours import ClimateProfile, ContourRegistry
from tests.test_climate_import import (
    complete_registry_payload,
    registry_payload,
    source_payload,
)


class MemoryStore:
    def __init__(self, registry: ClimateRegistry) -> None:
        self.registry = registry
        self.saved: list[ClimateRegistry] = []

    async def async_load(self) -> ClimateRegistry:
        return self.registry

    async def async_save(self, registry: ClimateRegistry) -> None:
        self.registry = registry
        self.saved.append(registry)


class RegistryRollbackFailingStore(MemoryStore):
    def __init__(self, registry: ClimateRegistry) -> None:
        super().__init__(registry)
        self.save_calls = 0

    async def async_save(self, registry: ClimateRegistry) -> None:
        self.save_calls += 1
        if self.save_calls == 2:
            raise RuntimeError("synthetic registry rollback failure")
        await super().async_save(registry)


class MemoryEvidenceStore:
    def __init__(self, evidence: ClimateShadowEvidence | None = None) -> None:
        self.evidence = evidence
        self.saved: list[dict[str, object]] = []

    async def async_load(self) -> ClimateShadowEvidence | None:
        return self.evidence

    async def async_save(self, evidence: ClimateShadowEvidence) -> None:
        self.evidence = evidence
        self.saved.append(evidence.as_storage_payload())


class MemoryContourStore:
    def __init__(self, registry: ContourRegistry | None = None) -> None:
        self.registry = registry or ContourRegistry()
        self.saved: list[ContourRegistry] = []
        self.fail = False

    async def async_load(self) -> ContourRegistry:
        return self.registry

    async def async_save(self, registry: ContourRegistry) -> None:
        if self.fail:
            raise RuntimeError("synthetic contour persistence failure")
        self.registry = registry
        self.saved.append(registry)


class MemoryProtectionStore:
    def __init__(self, memory: ClimateProtectionMemory | None = None) -> None:
        self.memory = memory
        self.saved: list[ClimateProtectionMemory] = []

    async def async_load(self) -> ClimateProtectionMemory | None:
        return self.memory

    async def async_save(self, memory: ClimateProtectionMemory) -> None:
        self.memory = memory
        self.saved.append(memory)


class FailingProtectionStore(MemoryProtectionStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    async def async_save(self, memory: ClimateProtectionMemory) -> None:
        if self.fail:
            raise RuntimeError("synthetic protection persistence failure")
        await super().async_save(memory)


class ContourRollbackFailingStore(MemoryContourStore):
    def __init__(self) -> None:
        super().__init__()
        self.save_calls = 0

    async def async_save(self, registry: ContourRegistry) -> None:
        self.save_calls += 1
        if self.save_calls == 2:
            raise RuntimeError("synthetic contour rollback failure")
        await super().async_save(registry)


class FailingEvidenceStore(MemoryEvidenceStore):
    def __init__(self) -> None:
        super().__init__()
        self.fail = False

    async def async_save(self, evidence: ClimateShadowEvidence) -> None:
        if self.fail:
            raise RuntimeError("synthetic evidence persistence failure")
        await super().async_save(evidence)


class MemoryBridge:
    def __init__(self) -> None:
        self.snapshot = import_climate_state(source_payload())
        self.fetch_count = 0
        self.executed = []

    async def async_fetch_state(self):
        self.fetch_count += 1
        return self.snapshot

    async def async_execute(self, plan):
        self.executed.append(plan)
        return {"ok": True}


class RejectingBridge(MemoryBridge):
    async def async_execute(self, plan):
        self.executed.append(plan)
        raise ClimateCommandRejected("synthetic explicit rejection")


class AmbiguousBridge(MemoryBridge):
    async def async_execute(self, plan):
        self.executed.append(plan)
        raise RuntimeError("synthetic transport ambiguity")


class ReflectingContourBridge(MemoryBridge):
    def __init__(self) -> None:
        super().__init__()
        self.payload = source_payload()
        self.payload["rooms"][0]["mode"] = "manual"  # type: ignore[index]
        self.payload["rooms"][0]["targets"]["temperature"] = 26  # type: ignore[index]
        self.payload["rooms"][0]["targets"]["targetStrategy"] = "soft"  # type: ignore[index]
        self.snapshot = import_climate_state(self.payload)

    async def async_execute(self, plan):
        self.executed.append(plan)
        room = self.payload["rooms"][0]  # type: ignore[index]
        if plan.action == "set_room_target_strategy":
            room["targets"]["targetStrategy"] = plan.backend_payload[  # type: ignore[index]
                "targetStrategy"
            ]
        elif plan.action == "set_room_target":
            room["targets"]["temperature"] = plan.backend_payload[  # type: ignore[index]
                "targetTemperature"
            ]
        elif plan.action == "set_room_mode":
            room["mode"] = plan.backend_payload["mode"]  # type: ignore[index]
        self.snapshot = import_climate_state(self.payload)
        return {"ok": True}


def configuration(mode: ClimateBridgeMode) -> SafeConfiguration:
    return SafeConfiguration(
        mode="shadow",
        climate_bridge_mode=mode,
        climate_bridge_target=climate_bridge_target("http://127.0.0.1:1880"),
        climate_canary_room_id=(
            "living" if mode is ClimateBridgeMode.CANARY else None
        ),
    )


def ready_evidence_store(
    payload: dict[str, object] | None = None,
) -> MemoryEvidenceStore:
    registry = registry_from_payload(payload or registry_payload())
    snapshot = import_climate_state(source_payload())
    start = 1784279405000
    evidence = ClimateShadowEvidence.for_registry(registry, now_ms=start)
    for offset in (0, 300_000, 600_000):
        evidence.record_observation(registry, snapshot, now_ms=start + offset)
    for action in ("set_room_target", "turn_room_off"):
        evidence.record_intent(
            category="translated",
            room_id="living",
            action=action,
            now_ms=start + 600_000,
        )
    return MemoryEvidenceStore(evidence)


class ClimateRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_setup_options_do_not_advance_or_save_shadow_evidence(self) -> None:
        bridge = MemoryBridge()
        registry = registry_from_payload(registry_payload())
        evidence_store = ready_evidence_store()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            bridge_client=bridge,
            evidence_store=evidence_store,
        )
        await runtime.async_start()
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]
        saves_before = list(evidence_store.saved)

        await runtime.async_climate_setup_options()

        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertEqual(saves_before, evidence_store.saved)
        self.assertEqual([], bridge.executed)

    async def test_contour_draft_reads_once_without_saving_or_commanding(self) -> None:
        bridge = MemoryBridge()
        registry = registry_from_payload({"version": 1, "rooms": [], "devices": []})
        registry_store = MemoryStore(registry)
        contour_store = MemoryContourStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count
        options = await runtime.async_climate_setup_options()
        revision = options["snapshot_revision"]
        self.assertEqual(fetches_before + 1, bridge.fetch_count)

        draft = await runtime.async_create_contour_draft(
            {
                "snapshot_revision": revision,
                "name": "Климат",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "kids",
                        "target_temperature": 25.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": "candidate_0001",
                                "type": "humidifier",
                            }
                        ],
                    }
                ],
            }
        )

        self.assertEqual("created", draft["status"])
        self.assertEqual(fetches_before + 2, bridge.fetch_count)
        validation = await runtime.async_validate_contour_draft(draft)
        self.assertEqual("ready", validation["status"])
        self.assertTrue(validation["save_allowed"])
        self.assertFalse(validation["command_allowed"])
        self.assertEqual(fetches_before + 3, bridge.fetch_count)
        self.assertEqual([], bridge.executed)
        self.assertEqual([], registry_store.saved)
        self.assertEqual([], contour_store.saved)

    async def test_contour_draft_saves_rooms_devices_and_parameters_together(
        self,
    ) -> None:
        bridge = MemoryBridge()
        original_registry = ClimateRegistry()
        registry_store = MemoryStore(original_registry)
        contour_store = MemoryContourStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        options = await runtime.async_climate_setup_options()
        draft = await runtime.async_create_contour_draft(
            {
                "snapshot_revision": options["snapshot_revision"],
                "name": "Климат дома",
                "mode": "automatic",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 25.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": "candidate_0002",
                                "type": "air_conditioner",
                            }
                        ],
                    },
                    {
                        "room_id": "kids",
                        "target_temperature": 24.0,
                        "target_humidity": 50,
                        "strategy": "soft",
                        "devices": [
                            {
                                "candidate_id": "candidate_0001",
                                "type": "humidifier",
                            }
                        ],
                    },
                ],
            }
        )
        saves_before = len(registry_store.saved)
        contour_saves_before = len(contour_store.saved)

        receipt = await runtime.async_save_contour_draft(draft)

        self.assertEqual("saved", receipt["status"])
        self.assertFalse(receipt["commands_sent"])
        self.assertFalse(receipt["restart_required"])
        self.assertEqual({"room_count": 2, "device_count": 2}, receipt["summary"])
        self.assertEqual(saves_before + 1, len(registry_store.saved))
        self.assertEqual(contour_saves_before + 1, len(contour_store.saved))
        self.assertEqual(["kids", "living"], [room.room_id for room in registry_store.registry.rooms])
        contour = contour_store.registry.contour("climate")
        self.assertIsNotNone(contour)
        self.assertEqual("existing_climate_core", contour.engine.value)  # type: ignore[union-attr]
        self.assertEqual(
            [24.0, 25.0],
            [room.day_profile.target_temperature for room in contour.rooms],  # type: ignore[union-attr]
        )
        current = await runtime.async_current_contour_setup()
        self.assertEqual("ready", current["status"])
        self.assertTrue(current["editing_allowed"])
        self.assertEqual("Климат дома", current["name"])
        self.assertEqual(2, current["summary"]["room_count"])  # type: ignore[index]
        self.assertEqual(saves_before + 1, len(registry_store.saved))
        self.assertEqual(contour_saves_before + 1, len(contour_store.saved))
        self.assertEqual([], bridge.executed)
        serialized = json.dumps(receipt, ensure_ascii=True, sort_keys=True)
        self.assertNotIn("synthetic-ac-source-living", serialized)

    async def test_contour_draft_save_rolls_back_everything_on_storage_failure(
        self,
    ) -> None:
        bridge = MemoryBridge()
        original_registry = ClimateRegistry()
        original_contours = ContourRegistry()
        registry_store = MemoryStore(original_registry)
        contour_store = MemoryContourStore(original_contours)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=registry_store,
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        options = await runtime.async_climate_setup_options()
        draft = await runtime.async_create_contour_draft(
            {
                "snapshot_revision": options["snapshot_revision"],
                "name": "Климат",
                "mode": "observe",
                "rooms": [
                    {
                        "room_id": "living",
                        "target_temperature": 25.0,
                        "target_humidity": 45,
                        "strategy": "normal",
                        "devices": [
                            {
                                "candidate_id": "candidate_0002",
                                "type": "air_conditioner",
                            }
                        ],
                    }
                ],
            }
        )
        contour_store.fail = True

        with self.assertRaisesRegex(RuntimeError, "contour persistence"):
            await runtime.async_save_contour_draft(draft)

        self.assertEqual(original_registry, registry_store.registry)
        self.assertEqual(original_contours, contour_store.registry)
        self.assertEqual([], bridge.executed)

    async def test_schedule_switches_profile_and_uses_existing_typed_executor_once(
        self,
    ) -> None:
        bridge = ReflectingContourBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_room_profiles(
            contours,
            {
                "living": {
                    "profiles": {
                        "day": {
                            "target_temperature": 25.0,
                            "target_humidity": 45,
                            "strategy": "normal",
                        },
                        "night": {
                            "target_temperature": 22.0,
                            "target_humidity": 40,
                            "strategy": "soft",
                        },
                    },
                    "active_profile": "day",
                }
            },
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contour_store = MemoryContourStore(contours)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            bridge_client=bridge,
            operation_id_factory=lambda: "9" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        receipt = await runtime.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 0)
        )
        repeated = await runtime.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 1)
        )

        self.assertIsNotNone(receipt)
        self.assertEqual("confirmed", receipt.status.value)  # type: ignore[union-attr]
        self.assertEqual(
            {
                "code": "apply_schedule_profile",
                "name": "Переключить профиль по расписанию",
                "room_id": None,
                "target_temperature": None,
                "profile": "night",
            },
            receipt.as_payload()["action"],  # type: ignore[union-attr]
        )
        self.assertIsNone(repeated)
        self.assertEqual(
            "night",
            contour_store.registry.contour("climate").rooms[0].active_profile.value,  # type: ignore[union-attr]
        )
        self.assertEqual(
            ["set_room_target", "set_room_mode"],
            [plan.action for plan in bridge.executed],
        )

    async def test_disabled_schedule_and_matching_period_send_no_commands(self) -> None:
        bridge = ReflectingContourBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
        )
        await runtime.async_start()

        result = await runtime.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 0)
        )
        with self.assertRaisesRegex(ContourApplyViolation, "schedule is not ready"):
            await runtime.async_temporary_temperature(
                {
                    "request_id": "temporary-without-schedule",
                    "contour_id": "climate",
                    "room_id": "living",
                    "action": "set",
                    "target_temperature": 23.5,
                    "confirm": True,
                },
                datetime(2026, 7, 19, 23, 0),
            )

        self.assertIsNone(result)
        self.assertEqual([], bridge.executed)

    async def test_new_schedule_applies_current_period_once_even_if_profile_matches(
        self,
    ) -> None:
        bridge = ReflectingContourBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contour_store = MemoryContourStore(contours)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            bridge_client=bridge,
        )
        await runtime.async_start()

        first = await runtime.async_run_climate_schedule(
            datetime(2026, 7, 19, 12, 0)
        )
        second = await runtime.async_run_climate_schedule(
            datetime(2026, 7, 19, 12, 1)
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            "day",
            contour_store.registry.contour(  # type: ignore[union-attr]
                "climate"
            ).schedule.last_applied_profile.value,
        )
        self.assertEqual(3, len(bridge.executed))

    async def test_temporary_temperature_applies_one_room_and_returns_to_schedule(
        self,
    ) -> None:
        bridge = ReflectingContourBridge()
        bridge.payload["rooms"][0]["mode"] = "auto"  # type: ignore[index]
        bridge.payload["rooms"][0]["targets"]["temperature"] = 25  # type: ignore[index]
        bridge.payload["rooms"][0]["targets"]["targetStrategy"] = "normal"  # type: ignore[index]
        bridge.snapshot = import_climate_state(bridge.payload)
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contours = with_active_climate_profile(contours, "day")
        contours = with_applied_climate_schedule_profile(
            contours,
            ClimateProfile.DAY,
        )
        contour_store = MemoryContourStore(contours)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            bridge_client=bridge,
            operation_id_factory=iter(("4" * 32, "5" * 32)).__next__,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        changed = await runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-1",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.5,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 0),
        )
        public = await runtime.async_contours_snapshot()

        self.assertEqual("confirmed", changed.status.value)
        self.assertEqual(1, changed.room_count)
        self.assertEqual(1, changed.command_count)
        room = contour_store.registry.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual(23.5, room.target_temperature)
        self.assertEqual(25.0, room.profile_settings.target_temperature)
        self.assertTrue(
            public["contours"][0]["rooms"][0]["temporary_temperature"][  # type: ignore[index]
                "active"
            ]
        )

        restored = await runtime.async_temporary_temperature(
            {
                "request_id": "temporary-living-clear-1",
                "contour_id": "climate",
                "room_id": "living",
                "action": "clear",
                "target_temperature": None,
                "confirm": True,
            },
            datetime(2026, 7, 19, 12, 5),
        )

        self.assertEqual("confirmed", restored.status.value)
        self.assertEqual(1, restored.command_count)
        room = contour_store.registry.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertIsNone(room.temporary_override)
        self.assertEqual(25.0, room.target_temperature)
        self.assertEqual(
            ["set_room_target", "set_room_target"],
            [plan.action for plan in bridge.executed],
        )

    async def test_next_schedule_period_clears_temporary_temperature(self) -> None:
        bridge = ReflectingContourBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_room_profiles(
            contours,
            {
                "living": {
                    "profiles": {
                        "day": {
                            "target_temperature": 25.0,
                            "target_humidity": 45,
                            "strategy": "normal",
                        },
                        "night": {
                            "target_temperature": 22.0,
                            "target_humidity": 40,
                            "strategy": "soft",
                        },
                    },
                    "active_profile": "day",
                }
            },
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contours = with_applied_climate_schedule_profile(
            contours,
            ClimateProfile.DAY,
        )
        contour_store = MemoryContourStore(contours)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            bridge_client=bridge,
            operation_id_factory=iter(("6" * 32, "7" * 32)).__next__,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        await runtime.async_temporary_temperature(
            {
                "request_id": "temporary-before-night",
                "contour_id": "climate",
                "room_id": "living",
                "action": "set",
                "target_temperature": 23.5,
                "confirm": True,
            },
            datetime(2026, 7, 19, 22, 59),
        )

        receipt = await runtime.async_run_climate_schedule(
            datetime(2026, 7, 19, 23, 0)
        )

        self.assertEqual("confirmed", receipt.status.value)  # type: ignore[union-attr]
        room = contour_store.registry.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertIsNone(room.temporary_override)
        self.assertEqual("night", room.active_profile.value)
        self.assertEqual(22.0, room.target_temperature)

    async def test_ambiguous_temporary_command_is_persisted_and_never_reposted(
        self,
    ) -> None:
        bridge = AmbiguousBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        contours = with_applied_climate_schedule_profile(
            contours,
            ClimateProfile.DAY,
        )
        contour_store = MemoryContourStore(contours)
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=contour_store,
            bridge_client=bridge,
            operation_id_factory=lambda: "8" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        request = {
            "request_id": "temporary-ambiguous",
            "contour_id": "climate",
            "room_id": "living",
            "action": "set",
            "target_temperature": 23.5,
            "confirm": True,
        }

        first = await runtime.async_temporary_temperature(
            request,
            datetime(2026, 7, 19, 12, 0),
        )
        second = await runtime.async_temporary_temperature(
            request,
            datetime(2026, 7, 19, 12, 1),
        )
        conflicting = {**request, "target_temperature": 24.0}
        with self.assertRaisesRegex(ContourApplyViolation, "already used"):
            await runtime.async_temporary_temperature(
                conflicting,
                datetime(2026, 7, 19, 12, 2),
            )

        self.assertEqual("unavailable", first.status.value)
        self.assertEqual(first, second)
        self.assertEqual(1, len(bridge.executed))
        room = contour_store.registry.contour("climate").rooms[0]  # type: ignore[union-attr]
        self.assertEqual(23.5, room.target_temperature)
        self.assertEqual(25.0, room.profile_settings.target_temperature)

    async def test_contour_apply_posts_three_typed_changes_and_confirms_state(
        self,
    ) -> None:
        bridge = ReflectingContourBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            operation_id_factory=lambda: "1" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        public = await runtime.async_contours_snapshot()
        preview = await runtime.async_contour_apply_preview()
        receipt = await runtime.async_apply_contour(
            {
                "request_id": "apply-1",
                "contour_id": "climate",
                "confirm": True,
            }
        )
        duplicate = await runtime.async_apply_contour(
            {
                "request_id": "apply-1",
                "contour_id": "climate",
                "confirm": True,
            }
        )

        self.assertTrue(
            public["contours"][0]["execution"]["settings_apply"][  # type: ignore[index]
                "available"
            ]
        )
        self.assertEqual(3, preview["command_count"])
        self.assertEqual("confirmed", receipt.status.value)
        self.assertEqual(3, receipt.accepted_count)
        self.assertEqual(1, receipt.confirmed_room_count)
        self.assertEqual(receipt, duplicate)
        self.assertEqual(
            [
                "set_room_target_strategy",
                "set_room_target",
                "set_room_mode",
            ],
            [plan.action for plan in bridge.executed],
        )

    async def test_contour_apply_pending_retry_only_rereads_and_never_reposts(
        self,
    ) -> None:
        bridge = MemoryBridge()
        payload = source_payload()
        payload["rooms"][0]["targets"]["temperature"] = 26  # type: ignore[index]
        bridge.snapshot = import_climate_state(payload)
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            operation_id_factory=lambda: "2" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        request = {
            "request_id": "apply-pending",
            "contour_id": "climate",
            "confirm": True,
        }

        first = await runtime.async_apply_contour(request)
        second = await runtime.async_apply_contour(request)

        self.assertEqual("pending", first.status.value)
        self.assertEqual("pending", second.status.value)
        self.assertEqual(1, len(bridge.executed))
        self.assertGreaterEqual(bridge.fetch_count, 4)

    async def test_ambiguous_contour_apply_is_reserved_and_not_retried(self) -> None:
        bridge = AmbiguousBridge()
        payload = source_payload()
        payload["rooms"][0]["targets"]["temperature"] = 26  # type: ignore[index]
        bridge.snapshot = import_climate_state(payload)
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.MANAGED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            operation_id_factory=lambda: "3" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        request = {
            "request_id": "apply-ambiguous",
            "contour_id": "climate",
            "confirm": True,
        }

        first = await runtime.async_apply_contour(request)
        second = await runtime.async_apply_contour(request)

        self.assertEqual("unavailable", first.status.value)
        self.assertEqual(first, second)
        self.assertEqual(1, len(bridge.executed))

    async def test_contour_setup_uses_existing_engine_and_never_posts(self) -> None:
        bridge = MemoryBridge()
        climate_store = MemoryStore(ClimateRegistry())
        contour_store = MemoryContourStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=climate_store,
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )

        await runtime.async_replace_contour_setup(
            registry_to_payload(registry),
            contour_registry_to_payload(contours),
        )
        result = await runtime.async_contours_snapshot()

        self.assertEqual("ready", result["contours"][0]["status"])  # type: ignore[index]
        self.assertTrue(
            result["contours"][0]["execution"]["automatic_active"]  # type: ignore[index]
        )
        self.assertFalse(
            result["contours"][0]["execution"]["settings_apply"][  # type: ignore[index]
                "available"
            ]
        )
        self.assertEqual([], bridge.executed)
        self.assertEqual(contours, contour_store.registry)

    async def test_disabled_contour_snapshot_performs_no_bridge_io(self) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_contours_snapshot()

        self.assertEqual("unavailable", result["contours"][0]["status"])  # type: ignore[index]
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_public_home_combines_room_and_contour_from_one_refresh(self) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contours = with_climate_schedule(
            contours,
            enabled=True,
            day_start="07:00",
            night_start="23:00",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
            local_now=lambda: datetime.fromisoformat(
                "2026-07-19T12:00:00+03:00"
            ),
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        result = await runtime.async_public_snapshot()

        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(12, result["contract"]["version"])  # type: ignore[index]
        self.assertEqual("climate", result["contours"][0]["id"])  # type: ignore[index]
        self.assertEqual(
            {
                "enabled": True,
                "day_start": "07:00",
                "night_start": "23:00",
                "next_profile": "night",
                "next_change_at": "2026-07-19T23:00:00+03:00",
            },
            result["contours"][0]["schedule"],  # type: ignore[index]
        )
        self.assertEqual(
            result["rooms"][0]["temperature"],  # type: ignore[index]
            result["contours"][0]["rooms"][0]["current"]["temperature"],  # type: ignore[index]
        )
        self.assertEqual([], bridge.executed)

    async def test_contour_store_failure_rolls_back_device_registry(self) -> None:
        bridge = MemoryBridge()
        original = ClimateRegistry()
        climate_store = MemoryStore(original)
        contour_store = MemoryContourStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=climate_store,
            contour_store=contour_store,
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="observe",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        contour_store.fail = True

        with self.assertRaisesRegex(RuntimeError, "contour persistence"):
            await runtime.async_replace_contour_setup(
                registry_to_payload(registry),
                contour_registry_to_payload(contours),
            )

        self.assertEqual(original, climate_store.registry)
        self.assertEqual(ContourRegistry(), contour_store.registry)
        self.assertEqual([], bridge.executed)

    async def test_evidence_failure_rolls_back_both_contour_stores(self) -> None:
        bridge = MemoryBridge()
        original_registry = ClimateRegistry()
        original_contours = ContourRegistry()
        climate_store = MemoryStore(original_registry)
        contour_store = MemoryContourStore(original_contours)
        evidence_store = FailingEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=climate_store,
            contour_store=contour_store,
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        evidence_store.fail = True

        with self.assertRaisesRegex(RuntimeError, "evidence persistence"):
            await runtime.async_replace_contour_setup(
                registry_to_payload(registry),
                contour_registry_to_payload(contours),
            )

        self.assertEqual(original_registry, climate_store.registry)
        self.assertEqual(original_contours, contour_store.registry)
        self.assertEqual([], bridge.executed)

    async def test_failed_registry_rollback_keeps_new_stores_consistent(self) -> None:
        bridge = MemoryBridge()
        climate_store = RegistryRollbackFailingStore(ClimateRegistry())
        contour_store = MemoryContourStore()
        evidence_store = FailingEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=climate_store,
            contour_store=contour_store,
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        evidence_store.fail = True

        with self.assertRaisesRegex(
            ClimateRuntimeUnavailable,
            "rollback failed",
        ):
            await runtime.async_replace_contour_setup(
                registry_to_payload(registry),
                contour_registry_to_payload(contours),
            )

        self.assertEqual(registry, climate_store.registry)
        self.assertEqual(contours, contour_store.registry)
        self.assertEqual(
            contour_registry_to_payload(contours),
            await runtime.async_contour_registry_payload(),
        )
        self.assertEqual([], bridge.executed)

    async def test_failed_contour_rollback_compensates_with_new_registry(self) -> None:
        bridge = MemoryBridge()
        climate_store = MemoryStore(ClimateRegistry())
        contour_store = ContourRollbackFailingStore()
        evidence_store = FailingEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=climate_store,
            contour_store=contour_store,
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        evidence_store.fail = True

        with self.assertRaisesRegex(
            ClimateRuntimeUnavailable,
            "rollback failed",
        ):
            await runtime.async_replace_contour_setup(
                registry_to_payload(registry),
                contour_registry_to_payload(contours),
            )

        self.assertEqual(registry, climate_store.registry)
        self.assertEqual(contours, contour_store.registry)
        self.assertEqual([], bridge.executed)

    async def test_native_preview_reads_state_but_never_posts(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_native_climate_preview(
            native_climate_policy("preview", "living", 22.0, 45)
        )

        self.assertEqual("ready", result["status"])
        self.assertEqual("cooling", result["decision"]["temperature"])  # type: ignore[index]
        self.assertFalse(result["execution"]["commands_enabled"])  # type: ignore[index]
        self.assertEqual([], bridge.executed)

    async def test_native_preview_with_disabled_bridge_performs_no_io(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_native_climate_preview(
            native_climate_policy("preview", "living", 22.0, 45)
        )

        self.assertEqual("unavailable", result["status"])
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_contour_targets_read_state_but_never_post(self) -> None:
        bridge = MemoryBridge()
        evidence_store = ready_evidence_store()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]
        saves_before = list(evidence_store.saved)

        result = await runtime.async_native_climate_targets()

        self.assertIsNotNone(result)
        self.assertEqual(25.0, result.room("living").target_temperature)  # type: ignore[union-attr]
        self.assertIs(
            result.room("living").observation_status,  # type: ignore[union-attr]
            ClimateDataStatus.FRESH,
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertEqual(saves_before, evidence_store.saved)
        self.assertEqual([], bridge.executed)

    async def test_native_contour_targets_keep_stale_status_without_posting(self) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        payload = source_payload()
        bridge.snapshot = import_climate_state(
            payload,
            now_ms=payload["generatedAt"] + 5 * 60 * 1000 + 1,  # type: ignore[operator]
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280300001,
        )
        await runtime.async_start()

        result = await runtime.async_native_climate_targets()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").observation_status,  # type: ignore[union-attr]
            ClimateDataStatus.STALE,
        )
        self.assertEqual(25.0, result.room("living").target_temperature)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_disabled_native_contour_targets_do_not_read_or_post(self) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        # A snapshot retained from an earlier mode must not bypass DISABLED.
        runtime._snapshot = bridge.snapshot

        result = await runtime.async_native_climate_targets()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").observation_status,  # type: ignore[union-attr]
            ClimateDataStatus.UNAVAILABLE,
        )
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_contour_demands_read_once_without_evidence_or_posts(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = ready_evidence_store()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]

        result = await runtime.async_native_climate_demands()

        self.assertIsNotNone(result)
        self.assertEqual("required", result.room("living").cooling.value)  # type: ignore[union-attr]
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_disabled_native_contour_demands_ignore_retained_state(self) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        runtime._snapshot = bridge.snapshot

        result = await runtime.async_native_climate_demands()

        self.assertIsNotNone(result)
        self.assertEqual("unavailable", result.room("living").cooling.value)  # type: ignore[union-attr]
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_contour_resolution_reads_once_without_evidence_or_posts(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = ready_evidence_store()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]

        result = await runtime.async_native_climate_resolutions()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").thermal,  # type: ignore[union-attr]
            ClimateThermalResolution.COOLING,
        )
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_disabled_native_contour_resolution_ignores_retained_state(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        runtime._snapshot = bridge.snapshot

        result = await runtime.async_native_climate_resolutions()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").thermal,  # type: ignore[union-attr]
            ClimateThermalResolution.UNAVAILABLE,
        )
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_contour_equipment_reads_once_without_evidence_or_posts(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = ready_evidence_store()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]

        result = await runtime.async_native_climate_equipment()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").device("living_air_conditioner").action,  # type: ignore[union-attr]
            ClimateEquipmentAction.COOL,
        )
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_disabled_native_contour_equipment_ignores_retained_state(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        runtime._snapshot = bridge.snapshot

        result = await runtime.async_native_climate_equipment()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").device("living_air_conditioner").action,  # type: ignore[union-attr]
            ClimateEquipmentAction.UNAVAILABLE,
        )
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_climate_stability_reads_once_without_evidence_or_posts(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = ready_evidence_store()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]

        result = await runtime.async_native_climate_stability()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").device("living_air_conditioner").action,  # type: ignore[union-attr]
            ClimateStabilityAction.COOL,
        )
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_disabled_native_climate_stability_ignores_retained_state(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        runtime._snapshot = bridge.snapshot

        result = await runtime.async_native_climate_stability()

        self.assertIsNotNone(result)
        self.assertIs(
            result.room("living").device("living_air_conditioner").action,  # type: ignore[union-attr]
            ClimateStabilityAction.UNAVAILABLE,
        )
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_climate_protection_rearms_once_after_restart(
        self,
    ) -> None:
        bridge = MemoryBridge()
        bridge.snapshot = replace(
            bridge.snapshot,
            rooms=tuple(
                replace(room, temperature=24.0)
                if room.room_id == "living"
                else room
                for room in bridge.snapshot.rooms
            ),
        )
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        protection_store = MemoryProtectionStore()
        clock = [1784280005000]
        first_runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            protection_store=protection_store,
            bridge_client=bridge,
            now_ms=lambda: clock[0],
        )
        await first_runtime.async_start()

        first = await first_runtime.async_native_climate_stability()

        self.assertEqual(
            480,
            first.room("living")  # type: ignore[union-attr]
            .device("living_air_conditioner")
            .remaining_seconds,
        )
        self.assertIsNotNone(protection_store.memory)
        clock[0] += 2 * 60_000
        restarted_runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            protection_store=protection_store,
            bridge_client=bridge,
            now_ms=lambda: clock[0],
        )
        await restarted_runtime.async_start()

        rearmed = await restarted_runtime.async_native_climate_stability()
        clock[0] += 60_000
        continued = await restarted_runtime.async_native_climate_stability()

        self.assertEqual(
            480,
            rearmed.room("living")  # type: ignore[union-attr]
            .device("living_air_conditioner")
            .remaining_seconds,
        )
        self.assertEqual(
            420,
            continued.room("living")  # type: ignore[union-attr]
            .device("living_air_conditioner")
            .remaining_seconds,
        )
        self.assertFalse(continued.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_native_climate_protection_storage_failure_fails_closed(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        protection_store = FailingProtectionStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            protection_store=protection_store,
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        protection_store.fail = True

        with self.assertRaises(ClimateRuntimeUnavailable):
            await runtime.async_native_climate_stability()

        self.assertEqual("RuntimeError", runtime.last_error)
        self.assertEqual([], bridge.executed)

    async def test_native_climate_policy_reads_once_without_evidence_or_posts(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = ready_evidence_store()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count
        evidence_before = evidence_store.evidence.as_storage_payload()  # type: ignore[union-attr]

        result = await runtime.async_native_climate_policy()

        self.assertIsNotNone(result)
        room = result.room("living")  # type: ignore[union-attr]
        self.assertIs(room.policy, ClimateRoomPolicy.SAFETY_LOCKOUT)  # type: ignore[union-attr]
        self.assertEqual(
            ("living_air_conditioner",),
            room.safe_stop_device_ids,  # type: ignore[union-attr]
        )
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(
            evidence_before,
            evidence_store.evidence.as_storage_payload(),  # type: ignore[union-attr]
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_disabled_native_climate_policy_ignores_retained_state(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        runtime._snapshot = bridge.snapshot

        result = await runtime.async_native_climate_policy()

        self.assertIsNotNone(result)
        room = result.room("living")  # type: ignore[union-attr]
        self.assertIs(room.policy, ClimateRoomPolicy.SAFETY_LOCKOUT)  # type: ignore[union-attr]
        self.assertIs(
            room.devices[0].action,  # type: ignore[union-attr]
            ClimateFinalDeviceAction.UNAVAILABLE,
        )
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_native_climate_isolation_reads_once_and_keeps_both_rooms(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
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
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        result = await runtime.async_native_climate_isolation()

        self.assertIsNotNone(result)
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertEqual(2, result.available_policy_count)  # type: ignore[union-attr]
        self.assertTrue(
            all(
                room.status is ClimateRoomIsolationStatus.READY
                for room in result.rooms  # type: ignore[union-attr]
            )
        )
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_native_climate_comparison_reads_once_without_commands(
        self,
    ) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        result = await runtime.async_native_climate_comparison()

        self.assertIsNotNone(result)
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        room = result.room("living")  # type: ignore[union-attr]
        self.assertIs(room.status, ClimateComparisonStatus.DIVERGED)
        self.assertEqual(
            (ClimateComparisonReason.DEVICE_ACTIVITY_MISMATCH,),
            room.reasons,
        )
        self.assertEqual(("living",), result.diverged_room_ids)  # type: ignore[union-attr]
        self.assertEqual([], bridge.executed)

    async def test_native_climate_ha_calls_stay_translation_only(self) -> None:
        bridge = MemoryBridge()
        registry, contours = build_climate_contour_setup(
            bridge.snapshot,
            room_ids=["living"],
            source_ids=["synthetic-ac-source-living"],
            name="Климат",
            mode="automatic",
            target_temperature=25.0,
            target_humidity=45,
            strategy="normal",
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry),
            contour_store=MemoryContourStore(contours),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        result = await runtime.async_native_climate_ha_calls()

        self.assertIsNotNone(result)
        self.assertEqual(fetches_before + 1, bridge.fetch_count)
        self.assertFalse(result.commands_enabled)  # type: ignore[union-attr]
        self.assertEqual(0, result.call_count)  # type: ignore[union-attr]
        room = result.room("living")  # type: ignore[union-attr]
        self.assertEqual(
            (ClimateHaCallLimit.MISSING_CONTROL_ENDPOINT,),
            room.devices[0].limits,
        )
        self.assertEqual([], bridge.executed)

    async def test_shadow_refreshes_but_never_posts(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: "0" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_action(
            {
                "request_id": "shadow-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        self.assertEqual("accepted", result.status)
        self.assertEqual("shadow", result.execution)
        self.assertEqual("0" * 32, result.operation_id)
        self.assertEqual([], bridge.executed)
        self.assertGreaterEqual(bridge.fetch_count, 2)

    async def test_shadow_collects_persistent_candidate_evidence_without_posts(self) -> None:
        bridge = MemoryBridge()
        now = [1784280005000]
        evidence_store = MemoryEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=evidence_store,
            operation_id_factory=iter(("a" * 32, "b" * 32)).__next__,
            now_ms=lambda: now[0],
        )
        await runtime.async_start()
        for _ in range(2):
            now[0] += 300_000
            await runtime.async_public_snapshot()
        await runtime.async_action(
            {
                "request_id": "evidence-target",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.0,
            }
        )
        await runtime.async_action(
            {
                "request_id": "evidence-off",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )

        evidence = await runtime.async_shadow_evidence({"room_id": "living"})

        self.assertTrue(evidence["candidate"]["ready"])  # type: ignore[index]
        self.assertEqual(3, evidence["counts"]["matched"])  # type: ignore[index]
        self.assertEqual(2, evidence["counts"]["translated"])  # type: ignore[index]
        self.assertGreaterEqual(len(evidence_store.saved), 6)
        self.assertEqual([], bridge.executed)

    async def test_shadow_evidence_query_persists_one_new_sample_once(self) -> None:
        bridge = MemoryBridge()
        now = [1784280005000]
        evidence_store = MemoryEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: now[0],
        )
        await runtime.async_start()
        saves_before_query = len(evidence_store.saved)
        now[0] += 300_000

        await runtime.async_shadow_evidence({"room_id": "living"})

        self.assertEqual(saves_before_query + 1, len(evidence_store.saved))

    async def test_shadow_canary_preflight_is_read_only_and_non_activating(self) -> None:
        bridge = MemoryBridge()
        registry = complete_registry_payload()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry)),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(registry),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        fetches_before = bridge.fetch_count

        result = await runtime.async_canary_preflight({"room_id": "living"})

        self.assertEqual("ready", result["status"])
        self.assertTrue(result["ready_for_authorization"])
        self.assertFalse(result["activation"]["allowed"])  # type: ignore[index]
        self.assertGreater(bridge.fetch_count, fetches_before)
        self.assertEqual([], bridge.executed)

    async def test_disabled_canary_preflight_never_fetches_or_activates(self) -> None:
        bridge = MemoryBridge()
        registry = complete_registry_payload()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.DISABLED),
            registry_store=MemoryStore(registry_from_payload(registry)),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(registry),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_canary_preflight({"room_id": "living"})

        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["ready_for_authorization"])
        self.assertFalse(result["freshness"]["state_fresh"])  # type: ignore[index]
        self.assertEqual(0, bridge.fetch_count)
        self.assertEqual([], bridge.executed)

    async def test_canary_preflight_reports_pending_and_requires_rollback(self) -> None:
        bridge = MemoryBridge()
        registry = complete_registry_payload()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry)),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(registry),
            operation_id_factory=lambda: "d" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        await runtime.async_action(
            {
                "request_id": "preflight-pending",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        result = await runtime.async_canary_preflight({"room_id": "living"})

        self.assertEqual("blocked", result["status"])
        self.assertEqual("pending", result["operation"]["status"])  # type: ignore[index]
        self.assertFalse(result["rollback"]["ready"])  # type: ignore[index]
        self.assertIn("preflight_requires_shadow", result["reasons"])
        self.assertIn("pending_operation", result["reasons"])
        self.assertEqual(1, len(bridge.executed))

    async def test_rejected_intent_keeps_validation_error_when_evidence_save_fails(
        self,
    ) -> None:
        bridge = MemoryBridge()
        evidence_store = FailingEvidenceStore()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=evidence_store,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        evidence_store.fail = True

        with self.assertRaisesRegex(ClimateCommandViolation, "unsupported"):
            await runtime.async_action(
                {
                    "request_id": "invalid-shadow-action",
                    "action": "unsupported",
                }
            )

        self.assertEqual("RuntimeError", runtime.last_error)
        self.assertEqual([], bridge.executed)

    async def test_canary_without_completed_shadow_evidence_fails_closed(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        snapshot = await runtime.async_public_snapshot()
        with self.assertRaisesRegex(ClimateCommandViolation, "evidence"):
            await runtime.async_action(
                {
                    "request_id": "blocked-canary",
                    "action": "turn_room_off",
                    "room_id": "living",
                }
            )

        self.assertFalse(snapshot["climate"]["commands_enabled"])  # type: ignore[index]
        self.assertEqual(12, snapshot["contract"]["version"])  # type: ignore[index]
        self.assertEqual(
            [],
            snapshot["rooms"][0]["control"]["allowed_actions"],  # type: ignore[index]
        )
        self.assertIn(
            "evidence_not_ready",
            snapshot["rooms"][0]["control"]["action_availability"][  # type: ignore[index]
                "set_room_target"
            ]["blocked_reasons"],
        )
        self.assertIn(
            "evidence_not_ready",
            snapshot["rooms"][0]["control"]["blocked_reasons"],  # type: ignore[index]
        )
        self.assertEqual([], bridge.executed)

    async def test_public_room_control_closes_while_canary_operation_is_pending(
        self,
    ) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "1" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        ready = await runtime.async_public_snapshot()
        await runtime.async_action(
            {
                "request_id": "pending-public-control",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )
        pending = await runtime.async_public_snapshot()

        self.assertTrue(ready["rooms"][0]["control"]["enabled"])  # type: ignore[index]
        self.assertNotEqual(
            ready["state_revision"],
            pending["state_revision"],
        )
        self.assertEqual(
            ["set_room_target", "turn_room_off"],
            ready["rooms"][0]["control"]["allowed_actions"],  # type: ignore[index]
        )
        self.assertTrue(
            ready["rooms"][0]["control"]["action_availability"][  # type: ignore[index]
                "turn_room_off"
            ]["allowed"]
        )
        self.assertTrue(ready["climate"]["commands_enabled"])  # type: ignore[index]
        self.assertFalse(pending["rooms"][0]["control"]["enabled"])  # type: ignore[index]
        self.assertEqual(
            [],
            pending["rooms"][0]["control"]["allowed_actions"],  # type: ignore[index]
        )
        self.assertEqual(
            ["operation_pending"],
            pending["rooms"][0]["control"]["action_availability"][  # type: ignore[index]
                "set_room_target"
            ]["blocked_reasons"],
        )
        self.assertEqual(
            ["operation_pending"],
            pending["rooms"][0]["control"]["blocked_reasons"],  # type: ignore[index]
        )
        self.assertFalse(pending["climate"]["commands_enabled"])  # type: ignore[index]
        self.assertEqual(1, len(bridge.executed))

    async def test_canary_posts_only_evidence_qualified_private_plan(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "1" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        result = await runtime.async_action(
            {
                "request_id": "canary-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        self.assertEqual("pending", result.status)
        self.assertEqual("living", result.room_id)
        self.assertEqual(1, len(bridge.executed))
        self.assertNotIn("deviceId", result.as_payload())

        with self.assertRaisesRegex(ClimateCommandViolation, "canary scope"):
            await runtime.async_action(
                {
                    "request_id": "canary-device-power",
                    "action": "set_device_power",
                    "device_id": "living_ac",
                    "on": True,
                }
            )
        self.assertEqual(1, len(bridge.executed))

    async def test_duplicate_request_is_idempotent_and_conflicting_reuse_is_rejected(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "2" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        payload = {
            "request_id": "retry-1",
            "action": "set_room_target",
            "room_id": "living",
            "target_temperature": 24.5,
        }

        first = await runtime.async_action(payload)
        fetches_after_first = bridge.fetch_count
        duplicate = await runtime.async_action(dict(payload))

        self.assertEqual(first, duplicate)
        self.assertEqual(fetches_after_first, bridge.fetch_count)
        self.assertEqual(1, len(bridge.executed))
        with self.assertRaisesRegex(ClimateCommandViolation, "already used"):
            await runtime.async_action({**payload, "target_temperature": 24.0})
        self.assertEqual(1, len(bridge.executed))

    async def test_explicit_backend_rejection_is_a_terminal_idempotent_receipt(self) -> None:
        bridge = RejectingBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "7" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        payload = {
            "request_id": "reject-1",
            "action": "turn_room_off",
            "room_id": "living",
        }

        first = await runtime.async_action(payload)
        duplicate = await runtime.async_action(dict(payload))

        self.assertEqual("rejected", first.status)
        self.assertEqual(first, duplicate)
        self.assertEqual(1, len(bridge.executed))

    async def test_ambiguous_post_is_reserved_before_io_and_cannot_repeat(self) -> None:
        bridge = AmbiguousBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "9" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        payload = {
            "request_id": "ambiguous-1",
            "action": "turn_room_off",
            "room_id": "living",
        }

        with self.assertRaisesRegex(RuntimeError, "ambiguity"):
            await runtime.async_action(payload)
        retry = await runtime.async_action(dict(payload))

        self.assertEqual("pending", retry.status)
        self.assertEqual(1, len(bridge.executed))

    async def test_evicted_request_id_fails_closed_instead_of_repeating(self) -> None:
        bridge = MemoryBridge()
        operation_ids = iter(f"{value:032x}" for value in range(1, 259))
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            operation_id_factory=lambda: next(operation_ids),
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        first_payload = {
            "request_id": "bounded-0",
            "action": "set_room_target",
            "room_id": "living",
            "target_temperature": 24.5,
        }
        first = await runtime.async_action(first_payload)
        for value in range(1, 257):
            await runtime.async_action(
                {**first_payload, "request_id": f"bounded-{value}"}
            )
        fetches_before_retry = bridge.fetch_count

        with self.assertRaisesRegex(ClimateCommandViolation, "lifecycle"):
            await runtime.async_action(dict(first_payload))
        unknown = await runtime.async_operation({"operation_id": first.operation_id})

        self.assertEqual(fetches_before_retry, bridge.fetch_count)
        self.assertFalse(unknown.known)

    async def test_room_off_confirmation_tracks_the_planned_device_only(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "8" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        receipt = await runtime.async_action(
            {
                "request_id": "room-off-1",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )
        source = source_payload()
        source["devices"][1]["roomId"] = "living"
        bridge.snapshot = import_climate_state(source)

        still_pending = await runtime.async_operation(
            {"operation_id": receipt.operation_id}
        )
        source["devices"][0]["state"] = "off"
        bridge.snapshot = import_climate_state(source)
        confirmed = await runtime.async_operation(
            {"operation_id": receipt.operation_id}
        )

        self.assertEqual("pending", still_pending.status)
        self.assertEqual("confirmed", confirmed.status)
        self.assertEqual(1, len(bridge.executed))

    async def test_pending_room_operation_confirms_from_a_later_read_only_snapshot(self) -> None:
        bridge = MemoryBridge()
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: "3" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()
        receipt = await runtime.async_action(
            {
                "request_id": "confirm-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )
        source = source_payload()
        source["rooms"][0]["targets"]["temperature"] = 24.5  # type: ignore[index]
        bridge.snapshot = import_climate_state(source)

        confirmed = await runtime.async_operation(
            {"operation_id": receipt.operation_id}
        )

        self.assertEqual("confirmed", confirmed.status)
        self.assertEqual(1, len(bridge.executed))

    async def test_canary_blocks_a_second_room_submission_until_timeout(self) -> None:
        bridge = MemoryBridge()
        operation_ids = iter(("5" * 32, "6" * 32))
        now = [1784280005000]
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=MemoryStore(registry_from_payload(registry_payload())),
            bridge_client=bridge,
            evidence_store=ready_evidence_store(),
            operation_id_factory=lambda: next(operation_ids),
            now_ms=lambda: now[0],
        )
        await runtime.async_start()
        first = await runtime.async_action(
            {
                "request_id": "room-lock-1",
                "action": "set_room_target",
                "room_id": "living",
                "target_temperature": 24.5,
            }
        )

        with self.assertRaisesRegex(ClimateCommandViolation, "pending"):
            await runtime.async_action(
                {
                    "request_id": "room-lock-2",
                    "action": "turn_room_off",
                    "room_id": "living",
                }
            )
        self.assertEqual(1, len(bridge.executed))

        now[0] += 30_000
        second = await runtime.async_action(
            {
                "request_id": "room-lock-2",
                "action": "turn_room_off",
                "room_id": "living",
            }
        )
        first_after_timeout = await runtime.async_operation(
            {"operation_id": first.operation_id}
        )

        self.assertEqual("pending", second.status)
        self.assertEqual("timed_out", first_after_timeout.status)
        self.assertEqual(2, len(bridge.executed))

    async def test_unknown_operation_is_redacted_and_shadow_readiness_is_count_only(self) -> None:
        bridge = MemoryBridge()
        full_registry = registry_payload()
        full_registry["devices"].append(  # type: ignore[union-attr]
            {
                "id": "kids_humidifier",
                "name": "Kids humidifier",
                "room_id": "kids",
                "kind": "humidifier",
                "source_id": "synthetic-humidifier-source-kids",
                "control_scope": "observed",
                "control_owner": "observed",
                "capabilities": ["power", "target_humidity"],
                "endpoints": [
                    {
                        "role": "control",
                        "entity_id": "humidifier.synthetic_kids",
                    }
                ],
            }
        )
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=MemoryStore(registry_from_payload(full_registry)),
            bridge_client=bridge,
            operation_id_factory=lambda: "4" * 32,
            now_ms=lambda: 1784280005000,
        )
        await runtime.async_start()

        readiness = await runtime.async_readiness()
        unknown = await runtime.async_operation({"operation_id": "f" * 32})

        self.assertTrue(readiness["ready"])
        self.assertEqual([], readiness["reasons"])
        self.assertNotIn("source_id", json.dumps(readiness, sort_keys=True))
        self.assertFalse(unknown.known)
        self.assertEqual("unknown", unknown.status)
        self.assertIsNone(unknown.request_id)

    async def test_registry_preview_validates_without_saving_then_atomic_save_remains_separate(self) -> None:
        store = MemoryStore(ClimateRegistry())
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=store,
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        preview = await runtime.async_preview_registry(registry_payload())

        self.assertTrue(preview["save_allowed"])
        self.assertEqual([], store.saved)
        await runtime.async_replace_registry(registry_payload())
        self.assertEqual(1, len(store.saved))

    async def test_registry_replacement_is_exact_and_persisted(self) -> None:
        store = MemoryStore(ClimateRegistry())
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.SHADOW),
            registry_store=store,
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        result = await runtime.async_replace_registry(registry_payload())

        self.assertEqual(1, len(store.saved))
        self.assertEqual("living_ac", result["devices"][0]["id"])

    async def test_canary_cannot_change_registry_bindings(self) -> None:
        store = MemoryStore(registry_from_payload(registry_payload()))
        runtime = ClimateRuntime(
            entry_id="entry",
            configuration=configuration(ClimateBridgeMode.CANARY),
            registry_store=store,
            bridge_client=MemoryBridge(),
        )
        await runtime.async_start()

        with self.assertRaises(ClimateRuntimeUnavailable):
            await runtime.async_replace_registry(registry_payload())

        self.assertEqual([], store.saved)


if __name__ == "__main__":
    unittest.main()
