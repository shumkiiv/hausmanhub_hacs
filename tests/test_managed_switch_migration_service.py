from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from functools import wraps
import hashlib
import json
from pathlib import Path
import unittest
from types import SimpleNamespace


from custom_components.hausman_hub.application.managed_switch_migration import (
    MANIFEST_HASH,
    MIGRATION_MANIFEST,
    ManagedSwitchMigration,
    ManagedSwitchMigrationConflict,
    ManagedSwitchStartupCoordinator,
    _entries_with_sources,
    _initial_journal,
    _receipt,
)
from custom_components.hausman_hub.application.native_automation_migration import (
    HomeAssistantNativeAutomationAdapter,
    NATIVE_AUTOMATION_ENTITY_IDS,
    NativeAutomationMigration,
)


def test_full_manifest_uses_exact_verified_binding_ids() -> None:
    assert len(MIGRATION_MANIFEST) == 8
    assert all(entry.activation_ready is True for entry in MIGRATION_MANIFEST)
    by_id = {entry.scenario_id: entry for entry in MIGRATION_MANIFEST}
    assert by_id["system-bathroom-exhaust-controller"].input_target_ids == (
        "entity_a591e035e3e5b34f",
        "entity_d82766182d69dd51",
        "entity_436e12f71ce7b08b",
        "entity_c15f5df5382ee180",
    )
    assert by_id["system-storage-light-controller"].input_target_ids == (
        "entity_00dcf0ebdc0bc6cb",
        "entity_0ec37ef18b4b39a6",
    )
    assert by_id["system-cabinet-light-controller"].input_target_ids == (
        "entity_aeaf7c250c68e8c2",
        "entity_7ff6d09cfa68fa5a",
        "entity_5f3b4436fb7b6f2b",
        "entity_6b9ccdab9bb484b2",
    )
    assert by_id["system-curtains-privacy-controller"].input_target_ids == (
        "entity_8746cfd7f6f7103d",
        "entity_2da2065add6e2168",
        "entity_1e0b476b7d082cc0",
        "entity_9164132c7692d6f5",
    )
    assert all("0123456789abcdef" not in target for entry in MIGRATION_MANIFEST for target in entry.input_target_ids)
from custom_components.hausman_hub.application.managed_switch_binding_migration import (
    BINDING_MIGRATION_MANIFEST,
)
from custom_components.hausman_hub.application.scenario_node_red import NodeRedSourceConflict
from custom_components.hausman_hub.application.scenario_service import (
    ScenarioRevisionConflictError,
    ScenarioService,
    ScenarioServiceError,
    _updated_curtain_manual_wrappers,
    _verify_curtain_manual_wrappers,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
    ScenarioComparison,
    ScenarioDefinition,
    ScenarioExecutionBackend,
    ScenarioExecutionMode,
    ScenarioNodeRedGeneratedBy,
    ScenarioNodeRedMetadata,
    ScenarioNodeRedSyncStatus,
    ScenarioRegistry,
    ScenarioTrigger,
    ScenarioTriggerType,
)


_CREATE_IDS = tuple(
    item.scenario_id for item in MIGRATION_MANIFEST if item.operation == "create"
)
_REPLACED_SOURCE_IDS = tuple(
    item.scenario_id
    for item in MIGRATION_MANIFEST
    if item.operation == "replace"
    and item.legacy_source_hash != item.new_source_hash
)
_EXPECTED_UPDATE_IDS = {
    "scenario_manual_curtains_open",
    "scenario_manual_curtains_close",
}
_EXPECTED_DISABLE_IDS = {
    "system-bathroom-fan-off-night",
    "system-bathroom-fan-off-day-sustained",
    "system-bathroom-fan-day-light-1",
    "system-bathroom-fan-day-light-2",
    "system-bathroom-fan-night-light-1",
    "system-bathroom-fan-night-light-2",
    "system-bathroom-fan-morning-light-1",
    "system-bathroom-fan-morning-quiet",
    "system-office-evening-bright",
    "system-office-evening-dark",
    "system-office-evening-medium",
    "system-office-day-medium",
    "system-office-day-bright",
    "system-office-night-light",
    "system-office-day-low",
    "system-storage-light-on-presence",
    "system-storage-light-off-timer",
    "system-storage-light-off-confirmed",
    "system-toilet-fan-off-delay",
    "system-toilet-fan-day",
    "system-toilet-light-motion-night",
    "system-toilet-light-motion-evening",
    "system-toilet-light-motion",
    "scenario_msirqdih",
    "system-twilight-curtains-close",
    "scenario_kitchen_curtains_sunrise",
    "scenario_curtains_lights_living",
    "scenario_curtains_lights_service",
    "scenario_mssvmo6v",
}
_EXPECTED_PRESERVE_IDS = {
    "scenario-office-curtain-living-lights-close",
    "scenario-office-curtain-service-lights-close",
    "scenario_kitchen_ac_off_2200",
    "scenario_manual_ac_off",
    "scenario_manual_away",
    "scenario_manual_good_night",
    "scenario_manual_lights_off",
    "scenario_manual_water_close",
    "scenario_manual_water_open",
    "scenario_small_corridor_motion_after_sunset",
    "scenario_small_corridor_motion_low_light",
    "system-away-turn-off",
    "system-kitchen-curtains-open-weekday",
    "system-kitchen-curtains-open-weekend",
    "system-leak-bathroom-alert",
    "system-leak-extra-bathroom-alert",
    "system-leak-kitchen-alert",
    "system-leak-toilet-alert",
    "system-office-curtain-auto-close",
    "system-small-corridor-ambient-dark",
    "system-small-corridor-ambient-dark-bright",
    "system-small-corridor-ambient-dusk",
    "system-small-corridor-ambient-dusk-bright",
    "system-small-corridor-ambient-night-bright",
}


def test_independent_fixture_manual_curtain_wrappers_expand_without_mutating_before() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures/hausmanhub_scenario_consolidation_v1/scenarios58.json"
    )
    before_document = json.loads(fixture_path.read_text(encoding="utf-8"))
    original_bytes = fixture_path.read_bytes()
    before = ScenarioRegistry.from_storage(before_document)

    after = _updated_curtain_manual_wrappers(before)
    _verify_curtain_manual_wrappers(after)

    assert len(before.scenarios) == 58
    assert len(after.scenarios) == 58
    for scenario_id in (
        "scenario_manual_curtains_open", "scenario_manual_curtains_close"
    ):
        assert len(before.scenario(scenario_id).definition.actions) == 2
        assert len(after.scenario(scenario_id).definition.actions) == 4
        assert after.scenario(scenario_id).revision == before.scenario(scenario_id).revision + 1
    assert fixture_path.read_bytes() == original_bytes


def _runtime_source(item: object) -> str:
    return (
        Path(__file__).parents[1]
        / "custom_components"
        / "hausman_hub"
        / "managed_scenarios"
        / str(getattr(item, "source_file"))
    ).read_text(encoding="utf-8")


def _registry(
    *, migrated: set[str] = frozenset(), include_creates: bool = False
) -> ScenarioRegistry:
    baseline = _registry_from_inventory58()
    manifest = {item.scenario_id: item for item in MIGRATION_MANIFEST}
    scenarios: list[Scenario] = []
    for scenario in baseline.scenarios:
        entry = manifest.get(scenario.id)
        if entry is None or entry.operation != "replace" or scenario.id not in migrated:
            scenarios.append(scenario)
            continue
        metadata = scenario.definition.node_red
        assert metadata is not None
        scenarios.append(
            replace(
                scenario,
                definition=replace(
                    scenario.definition,
                    node_red=replace(
                        metadata,
                        flow_revision=metadata.flow_revision + 1,
                        source_hash=entry.new_source_hash,
                        input_target_ids=entry.input_target_ids,
                    ),
                ),
                revision=scenario.revision + 1,
            )
        )
    if include_creates:
        for entry in MIGRATION_MANIFEST:
            if entry.operation != "create":
                continue
            metadata = ScenarioNodeRedMetadata(
                flow_id=f"flow-{entry.scenario_id}",
                flow_revision=1,
                source_hash=entry.new_source_hash,
                generated_by=ScenarioNodeRedGeneratedBy.HAUSMAN,
                sync_status=ScenarioNodeRedSyncStatus.SYNCED,
                input_target_ids=entry.input_target_ids,
            )
            definition = ScenarioDefinition(
                version=1,
                execution_mode=ScenarioExecutionMode.RESTART,
                execution_backend=ScenarioExecutionBackend.NODE_RED,
                node_red=metadata,
                triggers=(ScenarioTrigger("manual", ScenarioTriggerType.MANUAL),),
                conditions=(),
                actions=(
                    ScenarioAction(
                        "notify",
                        ScenarioActionType.NOTIFICATION,
                        message="ok",
                    ),
                ),
            )
            scenarios.append(
                Scenario.from_definition(
                    entry.scenario_id,
                    entry.scenario_id,
                    definition,
                    group="system",
                    enabled=False,
                    revision=0,
                    protected=True,
                )
            )
    return ScenarioRegistry(scenarios=tuple(scenarios))


def _registry_from_inventory58() -> ScenarioRegistry:
    payload = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "hausmanhub_scenario_consolidation_v1" / "scenarios58.json").read_text()
    )
    registry = ScenarioRegistry.from_storage(payload)
    assert len(registry.scenarios) == 58
    assert sum(item.enabled for item in registry.scenarios) == 46
    return registry


class RegistryStore:
    def __init__(self, registry, *, fail=False, before_fail=None):
        self.registry = registry
        self.saved = []
        self.fail = fail
        self.before_fail = before_fail

    async def async_load(self):
        return self.registry

    async def async_save(self, value):
        self.saved.append(value)
        if self.fail:
            self.fail = False
            if self.before_fail:
                self.before_fail()
            raise OSError("registry save failed")
        self.registry = value


class Backend:
    def __init__(self, deployed=None):
        self.deployed = deployed or {
            item.scenario_id: item.legacy_source_hash
            for item in MIGRATION_MANIFEST
            if item.operation == "replace"
        }
        self.updated = []
        self.created = []
        self.replaced = []
        self.restored = []
        self.deleted = []
        self.commits = 0
        self.revisions = {scenario_id: "revision.stable" for scenario_id in self.deployed}
        self.sources = {
            scenario_id: f"legacy:{scenario_id}"
            for scenario_id in self.deployed
        }

    async def async_read_source(self, scenario_id, _flow_id):
        return {
            "source_hash": self.deployed[scenario_id],
            "source": self.sources[scenario_id],
        }

    async def async_reconcile_created_release_source(
        self, scenario_id, expected_source_hash
    ):
        if scenario_id not in self.deployed:
            return None
        assert self.deployed[scenario_id] == expected_source_hash
        return {
            "flow_id": f"flow-{scenario_id}",
            "flow_revision": 1,
            "global_revision": "revision.stable",
        }

    async def async_prepare_new_release_source(self, scenario_id, _title, source, expected):
        assert scenario_id not in self.deployed
        assert expected
        self.deployed[scenario_id] = expected
        self.sources[scenario_id] = source
        self.revisions[scenario_id] = "revision.stable"
        self.updated.append(scenario_id)
        self.created.append(scenario_id)
        return {"flow_id": f"flow-{scenario_id}", "flow_revision": 1}

    async def async_delete_managed_flow(self, scenario_id, _flow_id, *, expected_source_hash):
        assert self.deployed[scenario_id] == expected_source_hash
        del self.deployed[scenario_id]
        self.sources.pop(scenario_id, None)
        self.deleted.append(scenario_id)

    async def async_verify_managed_topology(self, scenario_id, _flow_id):
        return {
            "source_hash": self.deployed[scenario_id],
            "topology": "managed-three-node-v1",
            "revision": self.revisions[scenario_id],
        }

    async def async_update_source(self, scenario_id, _definition, _flow_id, source, expected, _catalog, *, validate_only):
        assert not validate_only and self.deployed[scenario_id] == expected
        entry = next(item for item in MIGRATION_MANIFEST if item.scenario_id == scenario_id)
        self.deployed[scenario_id] = entry.new_source_hash
        self.sources[scenario_id] = source
        self.updated.append(scenario_id)
        self.replaced.append(scenario_id)
        return {"saved": True, "proposed_source_hash": entry.new_source_hash, "previous_source": f"legacy:{scenario_id}"}

    async def async_prepare_release_source(
        self, scenario_id, definition, flow_id, source, expected, catalog
    ):
        return await self.async_update_source(
            scenario_id,
            definition,
            flow_id,
            source,
            expected,
            catalog,
            validate_only=False,
        )

    async def async_restore_source(self, scenario_id, _flow_id, _source, *, expected_current_hash):
        if self.deployed[scenario_id] != expected_current_hash:
            raise NodeRedSourceConflict(expected_current_hash, self.deployed[scenario_id])
        entry = next(item for item in MIGRATION_MANIFEST if item.scenario_id == scenario_id)
        self.deployed[scenario_id] = entry.legacy_source_hash
        self.sources[scenario_id] = _source
        self.restored.append(scenario_id)

    async def async_commit_last_prepare(self):
        self.commits += 1


def _service(store, backend, *, missing=None):
    targets = {target for item in MIGRATION_MANIFEST for target in item.input_target_ids}
    catalog = SimpleNamespace(
        devices=tuple(targets),
        device=lambda target: None if target == missing else SimpleNamespace(target_id=target),
    )
    return ScenarioService(None, store, catalog, node_red_backend=backend)


def _registry_with_inputs(
    registry: ScenarioRegistry,
    scenario_id: str,
    input_target_ids: tuple[str, ...],
) -> ScenarioRegistry:
    scenario = registry.scenario(scenario_id)
    assert scenario is not None and scenario.definition.node_red is not None
    replacement = replace(
        scenario,
        definition=replace(
            scenario.definition,
            node_red=replace(
                scenario.definition.node_red,
                input_target_ids=input_target_ids,
            ),
        ),
    )
    return ScenarioRegistry(
        scenarios=tuple(
            replacement if item.id == scenario_id else item
            for item in registry.scenarios
        )
    )


class MigrationReceiptStore:
    def __init__(self, value):
        self.value = value
        self.saved = []

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        self.saved.append(value)
        self.value = value


class NativeAutomationServices:
    def __init__(self, states: dict[str, object]) -> None:
        self.states = states
        self.calls: list[tuple[str, str, dict[str, object], str]] = []

    async def async_call(
        self, domain, service, data, *, blocking, context
    ) -> None:
        assert domain == "automation"
        assert blocking is True
        entity_id = data["entity_id"]
        self.calls.append((domain, service, dict(data), context.id))
        state = self.states[entity_id]
        state.state = "off" if service == "turn_off" else "on"
        state.context = SimpleNamespace(id=context.id)
        state.last_updated = datetime.now(UTC)


class NativeAutomationComponent:
    def __init__(self, configs: dict[str, dict[str, object]]) -> None:
        self.configs = configs

    def get_entity(self, entity_id: str) -> object:
        return SimpleNamespace(raw_config=self.configs[entity_id])


def _native_hass(
    *,
    state_overrides: dict[str, str] | None = None,
    context_prefix: str,
    updated_hour: int,
) -> tuple[object, NativeAutomationServices]:
    payload = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/hausmanhub_scenario_consolidation_v1/native18.json"
        ).read_text(encoding="utf-8")
    )
    fixture = {item["entity_id"]: item for item in payload["automations"]}
    states = {
        entity_id: SimpleNamespace(
            state=(
                item["state"]
                if state_overrides is None
                else state_overrides[entity_id]
            ),
            attributes={"id": item["definition"]["id"]},
            context=SimpleNamespace(id=f"{context_prefix}-{index}"),
            last_updated=datetime(
                2026, 9, 7, updated_hour, index, tzinfo=UTC
            ),
        )
        for index, (entity_id, item) in enumerate(fixture.items())
    }
    services = NativeAutomationServices(states)
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=services,
        data={
            "automation": NativeAutomationComponent(
                {
                    entity_id: item["definition"]
                    for entity_id, item in fixture.items()
                }
            )
        },
    )
    return hass, services


_PRODUCTION_TAMBUR_INPUTS = (
    "entity_10b78187426f8485",
    "entity_156050daca86aa6c",
    "entity_170c7a4e2505b803",
    "entity_5f3b4436fb7b6f2b",
    "entity_6b9ccdab9bb484b2",
    "entity_71859313239a14e4",
    "entity_b47991988cc6b9f3",
    "entity_cd0098e5ff95da46",
    "entity_fbdf27871edb89bf",
)


async def test_batch_migration_updates_three_sources_and_registry_once() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    assert await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST) == "completed"
    assert tuple(backend.replaced) == _REPLACED_SOURCE_IDS
    assert tuple(backend.created) == _CREATE_IDS
    assert set(backend.updated) == set(_REPLACED_SOURCE_IDS) | set(_CREATE_IDS)
    assert len(store.saved) == 1
    assert backend.commits == 0
    for entry in MIGRATION_MANIFEST:
        scenario = store.registry.scenario(entry.scenario_id)
        assert scenario.revision == (
            0 if entry.operation == "create" else entry.legacy_revision + 1
        )
        assert scenario.definition.node_red.source_hash == entry.new_source_hash
        assert scenario.definition.node_red.input_target_ids == entry.input_target_ids
    await service.async_finalize_managed_switch_migration(MIGRATION_MANIFEST)
    await service.async_commit_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.commits == 1


async def test_snapshot_three_to_eight_creates_only_absent_controllers_disabled() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "hausmanhub_scenario_consolidation_v1"
        / "scenarios58.json"
    )
    fixture_before = fixture_path.read_bytes()
    store = RegistryStore(_registry_from_inventory58())
    backend = Backend({
        item.scenario_id: item.legacy_source_hash
        for item in MIGRATION_MANIFEST
        if item.operation == "replace"
    })
    service = _service(store, backend)
    await service.async_load()

    await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)

    created = [item for item in MIGRATION_MANIFEST if item.operation == "create"]
    assert {item.scenario_id for item in created} <= set(backend.updated)
    assert len(store.registry.scenarios) == 63
    for entry in created:
        scenario = store.registry.scenario(entry.scenario_id)
        assert scenario is not None
        assert scenario.enabled is False
        assert scenario.definition.node_red is not None
        assert scenario.definition.node_red.flow_id == f"flow-{entry.scenario_id}"

    await service.async_finalize_managed_switch_migration(MIGRATION_MANIFEST)
    await service.async_commit_managed_switch_migration(MIGRATION_MANIFEST)
    for entry in MIGRATION_MANIFEST:
        scenario = store.registry.scenario(entry.scenario_id)
        assert scenario is not None
        assert scenario.definition.node_red is not None
        assert scenario.definition.node_red.source_hash == entry.new_source_hash
        assert scenario.definition.node_red.input_target_ids == entry.input_target_ids
    assert backend.commits == 1
    assert fixture_path.read_bytes() == fixture_before


async def test_actual_startup_and_restart_apply_the_exact_registry_disposition() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures/hausmanhub_scenario_consolidation_v1/scenarios58.json"
    )
    original_bytes = fixture_path.read_bytes()
    original = _registry_from_inventory58()
    original_payloads = {
        item["id"]: item for item in original.to_storage()["scenarios"]
    }
    registry_store = RegistryStore(original)
    backend = Backend(
        {
            item.scenario_id: item.legacy_source_hash
            for item in MIGRATION_MANIFEST
            if item.operation == "replace"
        }
    )
    service = _service(registry_store, backend)
    await service.async_load()
    receipt_store = MigrationReceiptStore(None)
    native_receipt_store = MigrationReceiptStore(None)
    native_hass, native_services = _native_hass(
        context_prefix="initial", updated_hour=6
    )
    native_migration = NativeAutomationMigration(
        HomeAssistantNativeAutomationAdapter(native_hass),
        native_receipt_store,
    )
    activations = 0

    async def activate():
        nonlocal activations
        activations += 1
        return lambda: None

    coordinator = ManagedSwitchStartupCoordinator(
        service,
        ManagedSwitchMigration(
            service,
            receipt_store,
            native_automation_migration=native_migration,
        ),
        activate,
    )

    await coordinator.async_start()

    assert coordinator.ready is True
    assert activations == 1
    assert len(registry_store.saved) == 2
    assert len(registry_store.saved[0].scenarios) == 63
    assert sum(item.enabled for item in registry_store.saved[0].scenarios) == 17
    assert len(registry_store.saved[1].scenarios) == 63
    assert sum(item.enabled for item in registry_store.saved[1].scenarios) == 22
    final = registry_store.registry
    final_payloads = {
        item["id"]: item for item in final.to_storage()["scenarios"]
    }
    assert {
        scenario_id
        for scenario_id in original_payloads
        if original_payloads[scenario_id]["enabled"]
        and not final_payloads[scenario_id]["enabled"]
    } == _EXPECTED_DISABLE_IDS
    for scenario_id in _EXPECTED_DISABLE_IDS:
        before = original_payloads[scenario_id]
        after = final_payloads[scenario_id]
        assert after["enabled"] is False
        assert after["revision"] == before["revision"] + 1
        assert after["definition"] == before["definition"]
        assert {
            key: value
            for key, value in after.items()
            if key not in {"enabled", "revision", "updatedAt"}
        } == {
            key: value
            for key, value in before.items()
            if key not in {"enabled", "revision", "updatedAt"}
        }
    for scenario_id in _EXPECTED_PRESERVE_IDS:
        assert final_payloads[scenario_id] == original_payloads[scenario_id]
    for scenario_id in _EXPECTED_UPDATE_IDS:
        before = original_payloads[scenario_id]
        after = final_payloads[scenario_id]
        assert after["enabled"] == before["enabled"]
        assert after["revision"] == before["revision"] + 1
        assert len(after["definition"]["actions"]) == 4
    for item in MIGRATION_MANIFEST:
        scenario = final.scenario(item.scenario_id)
        assert scenario is not None
        assert scenario.enabled is True
        assert scenario.definition.node_red is not None
        assert scenario.definition.node_red.source_hash == item.new_source_hash
        assert scenario.definition.node_red.input_target_ids == item.input_target_ids
    assert backend.commits == 1
    assert receipt_store.value["state"] == "completed"
    assert native_receipt_store.value["state"] == "completed"
    assert len(native_services.calls) == len(NATIVE_AUTOMATION_ENTITY_IDS)
    assert fixture_path.read_bytes() == original_bytes

    restarted_service = _service(registry_store, backend)
    await restarted_service.async_load()
    restart_activations = 0
    restart_activation_ids: list[tuple[str, ...]] = []

    async def restart_activate():
        nonlocal restart_activations
        restart_activations += 1
        restart_activation_ids.append(
            tuple(
                item.scenario_id
                for item in MIGRATION_MANIFEST
                if registry_store.registry.scenario(item.scenario_id).enabled
            )
        )
        return lambda: None

    native_states = {
        entity_id: state.state
        for entity_id, state in native_services.states.items()
    }
    restarted_native_hass, restarted_native_services = _native_hass(
        state_overrides=native_states,
        context_prefix="restart",
        updated_hour=7,
    )
    assert all(
        restarted_native_services.states[entity_id]
        is not native_services.states[entity_id]
        for entity_id in native_states
    )

    restarted = ManagedSwitchStartupCoordinator(
        restarted_service,
        ManagedSwitchMigration(
            restarted_service,
            receipt_store,
            native_automation_migration=NativeAutomationMigration(
                HomeAssistantNativeAutomationAdapter(restarted_native_hass),
                native_receipt_store,
            ),
        ),
        restart_activate,
    )
    writes_before_restart = len(registry_store.saved)
    migration_receipt_writes_before_restart = len(receipt_store.saved)
    native_receipt_writes_before_restart = len(native_receipt_store.saved)
    backend_mutations_before_restart = (
        tuple(backend.updated),
        tuple(backend.created),
        tuple(backend.replaced),
        tuple(backend.restored),
        tuple(backend.deleted),
    )
    await restarted.async_start()

    assert restarted.ready is True
    assert restart_activations == 1
    assert restart_activation_ids == [
        tuple(item.scenario_id for item in MIGRATION_MANIFEST)
    ]
    assert len(registry_store.saved) == writes_before_restart
    assert len(receipt_store.saved) == migration_receipt_writes_before_restart
    assert (
        len(native_receipt_store.saved)
        == native_receipt_writes_before_restart
    )
    assert restarted_native_services.calls == []
    assert (
        tuple(backend.updated),
        tuple(backend.created),
        tuple(backend.replaced),
        tuple(backend.restored),
        tuple(backend.deleted),
    ) == backend_mutations_before_restart
    assert len(registry_store.registry.scenarios) == 63
    assert sum(item.enabled for item in registry_store.registry.scenarios) == 22
    assert fixture_path.read_bytes() == original_bytes


async def test_full_startup_accepts_permuted_equivalent_baseline_without_reordering() -> None:
    original = _registry_from_inventory58()
    permuted = ScenarioRegistry(
        scenarios=original.scenarios[19:] + original.scenarios[:19]
    )
    input_order = tuple(item.id for item in permuted.scenarios)
    input_payloads = {
        item["id"]: item for item in permuted.to_storage()["scenarios"]
    }
    registry_store = RegistryStore(permuted)
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    receipt_store = MigrationReceiptStore(None)
    activations = 0

    async def activate():
        nonlocal activations
        activations += 1
        return lambda: None

    coordinator = ManagedSwitchStartupCoordinator(
        service,
        ManagedSwitchMigration(service, receipt_store),
        activate,
        manifest=MIGRATION_MANIFEST,
    )

    await coordinator.async_start()

    final = registry_store.registry
    final_payloads = {
        item["id"]: item for item in final.to_storage()["scenarios"]
    }
    assert coordinator.ready is True
    assert activations == 1
    assert len(final.scenarios) == 63
    assert sum(item.enabled for item in final.scenarios) == 22
    assert tuple(
        item.id for item in final.scenarios if item.id not in _CREATE_IDS
    ) == input_order
    assert tuple(item.id for item in final.scenarios[-5:]) == _CREATE_IDS
    for scenario_id in _EXPECTED_PRESERVE_IDS:
        assert final_payloads[scenario_id] == input_payloads[scenario_id]


async def test_permuted_baseline_semantic_drift_fails_before_registry_or_source_write() -> None:
    original = _registry_from_inventory58()
    target = original.scenario("scenario_manual_good_night")
    assert target is not None
    for changed in (
        replace(target, revision=target.revision + 1),
        replace(
            target,
            definition=replace(
                target.definition,
                queue_limit=target.definition.queue_limit + 1,
            ),
        ),
    ):
        drifted = ScenarioRegistry(
            scenarios=tuple(
                changed if item.id == target.id else item
                for item in reversed(original.scenarios)
            )
        )
        registry_store = RegistryStore(drifted)
        backend = Backend()
        service = _service(registry_store, backend)
        await service.async_load()

        with unittest.TestCase().assertRaisesRegex(
            ScenarioServiceError, "approved migration baseline"
        ):
            await service.async_apply_managed_switch_migration(
                MIGRATION_MANIFEST
            )

        assert registry_store.saved == []
        assert backend.updated == []


async def test_create_failure_compensates_only_previously_created_flows() -> None:
    class FailSecondCreate(Backend):
        async def async_prepare_new_release_source(self, scenario_id, title, source, expected):
            if self.created:
                raise RuntimeError("Node-RED unavailable")
            return await super().async_prepare_new_release_source(scenario_id, title, source, expected)

    store = RegistryStore(_registry_from_inventory58())
    backend = FailSecondCreate({
        item.scenario_id: item.legacy_source_hash
        for item in MIGRATION_MANIFEST
        if item.operation == "replace"
    })
    service = _service(store, backend)
    await service.async_load()

    with unittest.TestCase().assertRaisesRegex(RuntimeError, "Node-RED unavailable"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)

    assert backend.deleted == ["system-toilet-comfort-controller"]
    assert tuple(backend.replaced) == _REPLACED_SOURCE_IDS
    assert tuple(backend.restored) == tuple(reversed(_REPLACED_SOURCE_IDS))
    assert all(
        backend.deployed[item.scenario_id] == item.legacy_source_hash
        and backend.sources[item.scenario_id] == f"legacy:{item.scenario_id}"
        for item in MIGRATION_MANIFEST
        if item.scenario_id in _REPLACED_SOURCE_IDS
    )
    shower = next(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id == "system-shower-comfort-controller"
    )
    assert shower.scenario_id in backend.restored
    assert backend.deployed[shower.scenario_id] == shower.legacy_source_hash
    assert backend.sources[shower.scenario_id] == f"legacy:{shower.scenario_id}"
    assert store.registry == _registry_from_inventory58()


async def test_replace_source_preparation_runs_outside_registry_lock() -> None:
    original = MIGRATION_MANIFEST[0]
    new_source = "// replacement source"
    replacement = replace(
        original,
        source=new_source,
        new_source_hash=hashlib.sha256(new_source.encode()).hexdigest(),
    )
    entries = (replacement, *MIGRATION_MANIFEST[1:])

    class SourceReplaceBackend(Backend):
        async def async_prepare_release_source(
            self, scenario_id, _definition, _flow_id, source, expected, _catalog
        ):
            if scenario_id != replacement.scenario_id:
                return await super().async_prepare_release_source(
                    scenario_id,
                    _definition,
                    _flow_id,
                    source,
                    expected,
                    _catalog,
                )
            assert not service._lock.locked()
            assert scenario_id == replacement.scenario_id
            assert source == new_source
            assert self.deployed[scenario_id] == expected
            previous_source = self.sources[scenario_id]
            self.deployed[scenario_id] = replacement.new_source_hash
            self.sources[scenario_id] = source
            self.updated.append(scenario_id)
            self.replaced.append(scenario_id)
            return {
                "saved": True,
                "proposed_source_hash": replacement.new_source_hash,
                "previous_source": previous_source,
            }

    store = RegistryStore(_registry())
    backend = SourceReplaceBackend()
    service = _service(store, backend)
    await service.async_load()

    await service.async_apply_managed_switch_migration(entries)

    assert replacement.scenario_id in backend.updated
    migrated = store.registry.scenario(replacement.scenario_id)
    assert migrated is not None and migrated.definition.node_red is not None
    assert migrated.definition.node_red.source_hash == replacement.new_source_hash
    assert service._managed_switch_replace_staging[replacement.scenario_id][1] == f"legacy:{replacement.scenario_id}"


async def test_replace_source_staging_restores_exact_source_after_cas_conflict() -> None:
    original = MIGRATION_MANIFEST[0]
    new_source = "// replacement source"
    replacement = replace(
        original,
        source=new_source,
        new_source_hash=hashlib.sha256(new_source.encode()).hexdigest(),
    )
    entries = (replacement, *MIGRATION_MANIFEST[1:])

    class SourceReplaceBackend(Backend):
        async def async_prepare_release_source(
            self, scenario_id, _definition, _flow_id, _source, expected, _catalog
        ):
            if scenario_id != replacement.scenario_id:
                return await super().async_prepare_release_source(
                    scenario_id,
                    _definition,
                    _flow_id,
                    _source,
                    expected,
                    _catalog,
                )
            assert self.deployed[scenario_id] == expected
            previous_source = self.sources[scenario_id]
            self.deployed[scenario_id] = replacement.new_source_hash
            self.sources[scenario_id] = _source
            self.updated.append(scenario_id)
            self.replaced.append(scenario_id)
            return {
                "saved": True,
                "proposed_source_hash": replacement.new_source_hash,
                "previous_source": previous_source,
            }

    original_registry = _registry()
    store = RegistryStore(original_registry)
    backend = SourceReplaceBackend()
    service = _service(store, backend)
    await service.async_load()
    journal = _initial_journal(
        await service.async_capture_managed_switch_migration(entries),
        entries,
    )
    await service.async_stage_managed_switch_migration(
        entries,
        journal=journal,
    )
    scenario = original_registry.scenario(replacement.scenario_id)
    assert scenario is not None
    service._registry = ScenarioRegistry(scenarios=tuple(
        replace(item, revision=99) if item.id == scenario.id else item
        for item in original_registry.scenarios
    ))

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "CAS evidence changed"
    ):
        await service.async_apply_managed_switch_migration(
            entries,
            journal=journal,
        )

    expected_restored = tuple(
        reversed(
            tuple(
                item.scenario_id
                for item in entries
                if item.operation == "replace"
                and item.legacy_source_hash != item.new_source_hash
            )
        )
    )
    assert tuple(backend.restored) == expected_restored
    for item in entries:
        if item.scenario_id in expected_restored:
            assert backend.deployed[item.scenario_id] == item.legacy_source_hash
            assert backend.sources[item.scenario_id] == f"legacy:{item.scenario_id}"
    assert service._managed_switch_replace_staging == {}


async def test_staged_flows_are_recovered_when_the_registry_cas_changes() -> None:
    """External preparation must not leave orphan flows after a CAS conflict."""

    original = _registry_from_inventory58()
    store = RegistryStore(original)
    backend = Backend({
        item.scenario_id: item.legacy_source_hash
        for item in MIGRATION_MANIFEST
        if item.operation == "replace"
    })
    service = _service(store, backend)
    await service.async_load()

    journal = _initial_journal(
        await service.async_capture_managed_switch_migration(
            MIGRATION_MANIFEST
        ),
        MIGRATION_MANIFEST,
    )
    await service.async_stage_managed_switch_migration(
        MIGRATION_MANIFEST,
        journal=journal,
    )
    first = original.scenario("system-shower-comfort-controller")
    assert first is not None
    service._registry = ScenarioRegistry(scenarios=(
        *(
            replace(item, revision=99) if item.id == first.id else item
            for item in original.scenarios
        ),
    ))

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "CAS evidence changed"
    ):
        await service.async_apply_managed_switch_migration(
            MIGRATION_MANIFEST,
            journal=journal,
        )

    assert set(backend.deleted) == {
        item.scenario_id for item in MIGRATION_MANIFEST if item.operation == "create"
    }
    assert tuple(backend.restored) == tuple(reversed(_REPLACED_SOURCE_IDS))
    assert all(
        backend.deployed[item.scenario_id] == item.legacy_source_hash
        and backend.sources[item.scenario_id] == f"legacy:{item.scenario_id}"
        for item in MIGRATION_MANIFEST
        if item.scenario_id in _REPLACED_SOURCE_IDS
    )
    assert service._managed_switch_create_staging == {}
    assert store.registry == original


async def test_prepared_restart_rejects_noncanonical_legacy_inputs() -> None:
    tambur = next(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id == "system-tambur-adaptive-controller"
    )
    permuted_inputs = _PRODUCTION_TAMBUR_INPUTS
    registry_store = RegistryStore(
        _registry_with_inputs(_registry(), tambur.scenario_id, permuted_inputs)
    )
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    receipt_store = MigrationReceiptStore(None)

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError,
        "approved migration baseline",
    ):
        await ManagedSwitchMigration(service, receipt_store).async_apply()

    assert receipt_store.value["state"] == "prepared"
    assert all(item["state"] == "prepared" for item in receipt_store.saved)
    assert backend.updated == []
    assert registry_store.saved == []


async def test_reused_staging_rejects_foreign_replacement_source_hash() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)
    await service.async_stage_managed_switch_migration(
        MIGRATION_MANIFEST,
        journal=journal,
    )
    changed_id = _REPLACED_SOURCE_IDS[0]
    backend.deployed[changed_id] = "f" * 64

    with unittest.TestCase().assertRaises(ScenarioRevisionConflictError):
        await service.async_stage_managed_switch_migration(
            MIGRATION_MANIFEST,
            journal=journal,
        )

    assert backend.deployed[changed_id] == "f" * 64
    assert service._managed_switch_replace_staging[changed_id][2] == next(
        item.new_source_hash
        for item in MIGRATION_MANIFEST
        if item.scenario_id == changed_id
    )


async def test_reused_staging_rejects_a_different_manifest_key_set() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)
    await service.async_stage_managed_switch_migration(
        MIGRATION_MANIFEST,
        journal=journal,
    )
    different_entries = tuple(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id != _CREATE_IDS[-1]
    )

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError,
        "does not match the manifest",
    ):
        await service.async_stage_managed_switch_migration(
            different_entries,
            journal=journal,
        )

    assert set(service._managed_switch_create_staging) == set(_CREATE_IDS)
    assert set(service._managed_switch_replace_staging) == set(
        _REPLACED_SOURCE_IDS
    )


async def test_reused_staging_rejects_actual_topology_drift() -> None:
    class DriftingTopologyBackend(Backend):
        drifted = False

        async def async_verify_managed_topology(self, scenario_id, flow_id):
            evidence = await super().async_verify_managed_topology(
                scenario_id,
                flow_id,
            )
            if self.drifted and scenario_id == _REPLACED_SOURCE_IDS[0]:
                evidence["topology"] = "foreign-topology"
            return evidence

    store = RegistryStore(_registry())
    backend = DriftingTopologyBackend()
    service = _service(store, backend)
    await service.async_load()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)
    await service.async_stage_managed_switch_migration(
        MIGRATION_MANIFEST,
        journal=journal,
    )
    backend.drifted = True

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError,
        "topology changed",
    ):
        await service.async_stage_managed_switch_migration(
            MIGRATION_MANIFEST,
            journal=journal,
        )

    assert tuple(backend.replaced) == _REPLACED_SOURCE_IDS


async def test_permuted_legacy_inputs_with_wrong_member_fail_before_mutation() -> None:
    tambur = next(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id == "system-tambur-adaptive-controller"
    )
    wrong_inputs = (
        "entity_not_in_release_manifest",
        *tambur.legacy_input_target_ids[:-1],
    )
    registry_store = RegistryStore(
        _registry_with_inputs(_registry(), tambur.scenario_id, wrong_inputs)
    )
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "approved migration baseline"
    ):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)

    assert backend.updated == []
    assert registry_store.saved == []


async def test_permuted_migrated_inputs_remain_a_strict_conflict() -> None:
    migrated_ids = {
        item.scenario_id
        for item in MIGRATION_MANIFEST
        if item.operation == "replace"
    }
    tambur = next(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id == "system-tambur-adaptive-controller"
    )
    permuted_inputs = (tambur.input_target_ids[-1], *tambur.input_target_ids[:-1])
    registry_store = RegistryStore(
        _registry_with_inputs(
            _registry(migrated=migrated_ids),
            tambur.scenario_id,
            permuted_inputs,
        )
    )
    backend = Backend(
        {
            item.scenario_id: item.new_source_hash
            for item in MIGRATION_MANIFEST
            if item.operation == "replace"
        }
    )
    service = _service(registry_store, backend)
    await service.async_load()

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "approved migration baseline"
    ):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)

    assert backend.updated == []
    assert registry_store.saved == []


async def test_snapshot_binding_is_not_replayed_after_the_v4_migration() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()

    await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    await service.async_verify_managed_switch_migration(MIGRATION_MANIFEST)
    await service.async_verify_managed_switch_migration(MIGRATION_MANIFEST)
    await service.async_finalize_managed_switch_migration(MIGRATION_MANIFEST)
    await service.async_commit_managed_switch_migration(MIGRATION_MANIFEST)


async def test_each_replace_crash_point_reconciles_without_redeploy() -> None:
    replacements = tuple(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id in _REPLACED_SOURCE_IDS
    )
    assert len(replacements) == 3
    for crash_index, partial in enumerate(replacements):
        for source_write_completed in (False, True):
            backend = Backend()
            store = RegistryStore(_registry())
            service = _service(store, backend)
            await service.async_load()
            before = await service.async_capture_managed_switch_migration(
                MIGRATION_MANIFEST
            )
            journal = _initial_journal(before, MIGRATION_MANIFEST)
            for completed in replacements[:crash_index]:
                journal["operations"][completed.scenario_id]["state"] = "applied"
                backend.deployed[completed.scenario_id] = completed.new_source_hash
                backend.sources[completed.scenario_id] = _runtime_source(completed)
            journal["operations"][partial.scenario_id]["state"] = "intent"
            if source_write_completed:
                backend.deployed[partial.scenario_id] = partial.new_source_hash
                backend.sources[partial.scenario_id] = _runtime_source(partial)

            await service.async_apply_managed_switch_migration(
                MIGRATION_MANIFEST,
                journal=journal,
            )

            first_not_deployed = crash_index + int(source_write_completed)
            assert tuple(backend.replaced) == tuple(
                item.scenario_id for item in replacements[first_not_deployed:]
            )
            for item in replacements:
                migrated = store.registry.scenario(item.scenario_id)
                assert migrated.revision == item.legacy_revision + 1
                assert (
                    migrated.definition.node_red.flow_revision
                    == item.legacy_revision + 1
                )


async def test_completed_registry_is_verified_without_any_mutation() -> None:
    backend = Backend()
    store = RegistryStore(_registry())
    service = _service(store, backend)
    await service.async_load()
    receipt = MigrationReceiptStore(None)
    assert await ManagedSwitchMigration(service, receipt).async_apply() == "completed"
    updated = list(backend.updated)
    registry_saves = len(store.saved)
    receipt_saves = len(receipt.saved)

    restarted = _service(store, backend)
    await restarted.async_load()
    assert await ManagedSwitchMigration(restarted, receipt).async_apply() == "completed"
    assert backend.updated == updated
    assert len(store.saved) == registry_saves
    assert len(receipt.saved) == receipt_saves


async def test_final_verification_requires_one_cross_scenario_cas_snapshot() -> None:
    backend = Backend()
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()
    await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    backend.revisions[MIGRATION_MANIFEST[-1].scenario_id] = "revision.drifted"

    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "snapshot changed"):
        await service.async_verify_managed_switch_migration(MIGRATION_MANIFEST)


async def test_conflict_or_missing_target_causes_no_mutation() -> None:
    registry = _registry()
    changed = registry.scenarios[0]
    registry = ScenarioRegistry(scenarios=(
        Scenario.from_definition(
            changed.id, changed.title, changed.definition, group="system",
            revision=99, protected=True,
        ), *registry.scenarios[1:],
    ))
    backend = Backend()
    service = _service(RegistryStore(registry), backend)
    await service.async_load()
    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "approved migration baseline"
    ):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []

    backend = Backend()
    backend.deployed[MIGRATION_MANIFEST[0].scenario_id] = "a" * 64
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()
    with unittest.TestCase().assertRaises(ScenarioRevisionConflictError):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []

    backend = Backend()
    service = _service(RegistryStore(_registry()), backend, missing=MIGRATION_MANIFEST[0].input_target_ids[0])
    await service.async_load()
    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "target is missing"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []


async def test_topology_or_non_protected_scenario_conflict_causes_no_mutation() -> None:
    class WrongTopology(Backend):
        async def async_verify_managed_topology(self, scenario_id, flow_id):
            evidence = await super().async_verify_managed_topology(scenario_id, flow_id)
            evidence["topology"] = "rerouted"
            return evidence

    backend = WrongTopology()
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()
    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "topology changed"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []

    registry = _registry()
    first = registry.scenarios[0]
    unprotected = Scenario.from_definition(
        first.id, first.title, first.definition, group="custom",
        revision=first.revision, protected=False,
    )
    backend = Backend()
    service = _service(
        RegistryStore(ScenarioRegistry(scenarios=(unprotected, *registry.scenarios[1:]))),
        backend,
    )
    await service.async_load()
    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "approved migration baseline"
    ):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []


async def test_duplicate_manifest_entry_is_rejected_before_any_mutation() -> None:
    backend = Backend()
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "manifest is invalid"
    ):
        await service.async_apply_managed_switch_migration(
            (*MIGRATION_MANIFEST, MIGRATION_MANIFEST[0])
        )

    assert backend.updated == []


async def test_registry_failure_compensates_sources_and_later_manual_edit_blocks_rollback() -> None:
    backend = Backend()
    store = RegistryStore(_registry(), fail=True)
    service = _service(store, backend)
    await service.async_load()
    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "write failed"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert set(backend.deleted) == {
        item.scenario_id for item in MIGRATION_MANIFEST if item.operation == "create"
    }
    assert tuple(backend.restored) == tuple(reversed(_REPLACED_SOURCE_IDS))
    assert all(
        backend.deployed[item.scenario_id] == item.legacy_source_hash
        and backend.sources[item.scenario_id] == f"legacy:{item.scenario_id}"
        for item in MIGRATION_MANIFEST
        if item.scenario_id in _REPLACED_SOURCE_IDS
    )


async def test_cancellation_after_first_source_mutation_restores_exact_source() -> None:
    class CancelAfterFirstMutation(Backend):
        def __init__(self) -> None:
            super().__init__()
            self.second_update_started = asyncio.Event()

        async def _pause_after_first_mutation(self) -> None:
            if self.updated:
                self.second_update_started.set()
                await asyncio.Event().wait()

        async def async_prepare_release_source(
            self, scenario_id, definition, flow_id, source, expected, catalog
        ):
            await self._pause_after_first_mutation()
            return await super().async_prepare_release_source(
                scenario_id, definition, flow_id, source, expected, catalog
            )

        async def async_prepare_new_release_source(
            self, scenario_id, title, source, expected
        ):
            await self._pause_after_first_mutation()
            return await super().async_prepare_new_release_source(
                scenario_id, title, source, expected
            )

    original = _registry()
    store = RegistryStore(original)
    backend = CancelAfterFirstMutation()
    service = _service(store, backend)
    await service.async_load()

    migration = asyncio.create_task(
        service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    )
    await backend.second_update_started.wait()
    migration.cancel()
    with unittest.TestCase().assertRaises(asyncio.CancelledError):
        await migration

    assert len(backend.updated) == 1
    assert backend.updated == [_REPLACED_SOURCE_IDS[0]]
    assert backend.created == []
    assert backend.deleted == []
    assert backend.restored == [_REPLACED_SOURCE_IDS[0]]
    first_replacement = next(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id == _REPLACED_SOURCE_IDS[0]
    )
    assert backend.deployed[first_replacement.scenario_id] == first_replacement.legacy_source_hash
    assert backend.sources[first_replacement.scenario_id] == f"legacy:{first_replacement.scenario_id}"
    assert store.registry == original
    assert service._managed_switch_migration_transaction is None


async def test_final_snapshot_drift_can_restore_exact_sources_and_registry() -> None:
    original = _registry()
    store = RegistryStore(original)
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    backend.revisions[MIGRATION_MANIFEST[-1].scenario_id] = "revision.drifted"

    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "snapshot changed"):
        await service.async_verify_managed_switch_migration(MIGRATION_MANIFEST)

    assert await service.async_rollback_managed_switch_migration(MIGRATION_MANIFEST)
    assert store.registry == original
    assert set(backend.deleted) == {
        item.scenario_id for item in MIGRATION_MANIFEST if item.operation == "create"
    }
    assert tuple(backend.restored) == tuple(reversed(_REPLACED_SOURCE_IDS))
    assert all(
        backend.deployed[item.scenario_id] == item.legacy_source_hash
        and backend.sources[item.scenario_id] == f"legacy:{item.scenario_id}"
        for item in MIGRATION_MANIFEST
        if item.scenario_id in _REPLACED_SOURCE_IDS
    )
    assert backend.commits == 1


async def test_manual_source_edit_rejects_final_rollback_without_overwrite() -> None:
    replacements = tuple(
        item
        for item in MIGRATION_MANIFEST
        if item.scenario_id in _REPLACED_SOURCE_IDS
    )
    for changed in replacements:
        store = RegistryStore(_registry())
        backend = Backend()
        service = _service(store, backend)
        await service.async_load()
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
        backend.deployed[changed.scenario_id] = "f" * 64

        assert not await service.async_rollback_managed_switch_migration(
            MIGRATION_MANIFEST
        )
        assert backend.deployed[changed.scenario_id] == "f" * 64
        assert backend.restored == []
        assert (
            store.registry.scenario(changed.scenario_id).revision
            == changed.legacy_revision + 1
        )


async def test_completed_receipt_failure_with_manual_edit_stays_prepared_and_blocked() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    first = MIGRATION_MANIFEST[0]

    class ReceiptStore:
        def __init__(self) -> None:
            self.value = None

        async def async_load(self):
            return self.value

        async def async_save(self, value):
            if value["state"] == "completed":
                backend.deployed[first.scenario_id] = "f" * 64
                raise OSError("receipt store failed")
            self.value = value

    receipt_store = ReceiptStore()
    with unittest.TestCase().assertRaisesRegex(
        ManagedSwitchMigrationConflict, "recovery"
    ):
        await ManagedSwitchMigration(service, receipt_store).async_apply()

    assert receipt_store.value["state"] == "prepared"
    assert backend.deployed[first.scenario_id] == "f" * 64
    assert backend.restored == []


async def test_replace_intent_contains_exact_rollback_source_before_write() -> None:
    original = MIGRATION_MANIFEST[0]
    source = "// durable replacement"
    replacement = replace(
        original,
        source=source,
        new_source_hash=hashlib.sha256(source.encode()).hexdigest(),
    )
    entries = (replacement, *MIGRATION_MANIFEST[1:])
    store = RegistryStore(_registry())
    snapshots = []

    class InspectIntentBackend(Backend):
        async def async_prepare_release_source(
            self, scenario_id, definition, flow_id, source, expected, catalog
        ):
            if scenario_id != replacement.scenario_id:
                operation = snapshots[-1][scenario_id]
                entry = next(
                    item
                    for item in entries
                    if item.scenario_id == scenario_id
                )
                assert operation == {
                    "kind": "replace",
                    "state": "intent",
                    "flowId": flow_id,
                    "flowRevision": None,
                    "expectedSourceHash": expected,
                    "newSourceHash": entry.new_source_hash,
                    "previousSource": f"legacy:{scenario_id}",
                }
                return await super().async_prepare_release_source(
                    scenario_id,
                    definition,
                    flow_id,
                    source,
                    expected,
                    catalog,
                )
            operation = snapshots[-1][scenario_id]
            assert operation == {
                "kind": "replace",
                "state": "intent",
                "flowId": flow_id,
                "flowRevision": None,
                "expectedSourceHash": expected,
                "newSourceHash": replacement.new_source_hash,
                "previousSource": f"legacy:{scenario_id}",
            }
            assert self.deployed[scenario_id] == expected
            self.deployed[scenario_id] = replacement.new_source_hash
            self.sources[scenario_id] = source
            self.updated.append(scenario_id)
            self.replaced.append(scenario_id)
            return {
                "saved": True,
                "proposed_source_hash": replacement.new_source_hash,
                "previous_source": f"legacy:{scenario_id}",
            }

    backend = InspectIntentBackend()
    service = _service(store, backend)
    await service.async_load()
    before = await service.async_capture_managed_switch_migration(entries)
    journal = _initial_journal(before, entries)

    async def persist(operations):
        snapshots.append(json.loads(json.dumps(operations)))

    await service.async_stage_managed_switch_migration(
        entries, journal=journal, on_staged=persist
    )
    assert snapshots
    assert tuple(backend.replaced) == tuple(
        item.scenario_id
        for item in entries
        if item.operation == "replace"
        and item.legacy_source_hash != item.new_source_hash
    )


async def test_exact_foreign_create_without_durable_intent_is_not_adopted_or_deleted() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)
    create_entry = next(
        item for item in MIGRATION_MANIFEST if item.operation == "create"
    )
    backend.deployed[create_entry.scenario_id] = create_entry.new_source_hash
    backend.sources[create_entry.scenario_id] = "foreign exact release source"
    backend.revisions[create_entry.scenario_id] = "revision.foreign"

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "without intent|conflicts"
    ):
        await service.async_stage_managed_switch_migration(
            MIGRATION_MANIFEST, journal=journal
        )

    assert create_entry.scenario_id in backend.deployed
    assert backend.deleted == []


async def test_before_capture_rejects_existing_create_endpoint() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    create_entry = next(
        item for item in MIGRATION_MANIFEST if item.operation == "create"
    )
    backend.deployed[create_entry.scenario_id] = create_entry.new_source_hash
    backend.sources[create_entry.scenario_id] = "foreign exact release source"
    backend.revisions[create_entry.scenario_id] = "revision.foreign"
    service = _service(store, backend)
    await service.async_load()

    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "endpoint already exists"
    ):
        await service.async_capture_managed_switch_migration(
            MIGRATION_MANIFEST
        )

    assert create_entry.scenario_id in backend.deployed
    assert backend.deleted == []


async def test_prepared_create_intent_adopts_lost_response_and_keeps_original_before() -> None:
    registry_store = RegistryStore(_registry())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)
    create_entry = next(
        item for item in MIGRATION_MANIFEST if item.operation == "create"
    )
    journal["operations"][create_entry.scenario_id]["state"] = "intent"
    backend.deployed[create_entry.scenario_id] = create_entry.new_source_hash
    backend.sources[create_entry.scenario_id] = "lost create response"
    backend.revisions[create_entry.scenario_id] = "revision.stable"
    receipt = MigrationReceiptStore(
        {**_receipt("prepared"), "journal": journal}
    )

    await ManagedSwitchMigration(service, receipt).async_apply()

    assert receipt.value["state"] == "completed"
    assert receipt.value["journal"]["before"] == before
    assert create_entry.scenario_id not in backend.updated


async def test_registry_write_has_durable_intent_and_lost_reply_is_reconciled() -> None:
    receipt = MigrationReceiptStore(None)

    class LostReplyRegistryStore(RegistryStore):
        def __init__(self, registry):
            super().__init__(registry)
            self.calls = 0

        async def async_save(self, value):
            self.calls += 1
            registry_record = receipt.value["journal"]["registry"]
            assert registry_record["state"] == "intent"
            assert isinstance(registry_record["afterHash"], str)
            self.registry = value
            self.saved.append(value)
            if self.calls == 1:
                raise OSError("registry response lost")

    registry_store = LostReplyRegistryStore(_registry())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()

    assert await ManagedSwitchMigration(service, receipt).async_apply() == "completed"
    assert receipt.value["state"] == "completed"


async def test_cancellation_after_registry_write_rolls_back_exactly_and_can_retry() -> None:
    receipt = MigrationReceiptStore(None)

    class CancelAfterRegistryWrite(RegistryStore):
        def __init__(self, registry):
            super().__init__(registry)
            self.cancelled = False

        async def async_save(self, value):
            registry_record = receipt.value["journal"]["registry"]
            assert registry_record["state"] == "intent"
            self.registry = value
            self.saved.append(value)
            if not self.cancelled:
                self.cancelled = True
                raise asyncio.CancelledError

    before_registry = _registry()
    registry_store = CancelAfterRegistryWrite(before_registry)
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()

    with unittest.TestCase().assertRaises(asyncio.CancelledError):
        await ManagedSwitchMigration(service, receipt).async_apply()

    assert registry_store.registry == before_registry
    assert receipt.value["state"] == "prepared"
    assert receipt.value["journal"]["registry"]["state"] == "pending"
    assert service._stopping is False
    assert service._managed_switch_blocked_runs == set()
    assert await ManagedSwitchMigration(service, receipt).async_apply() == "completed"


async def test_prepared_restart_before_registry_write_completes_exact_transition() -> None:
    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    entries = _entries_with_sources()
    before = await service.async_capture_managed_switch_migration(
        entries
    )
    journal = _initial_journal(before, entries)

    async def persist_operations(operations):
        journal["operations"] = json.loads(json.dumps(operations))

    await service.async_stage_managed_switch_migration(
        entries,
        journal=journal,
        on_staged=persist_operations,
    )
    assert registry_store.saved == []
    updates_before_restart = tuple(backend.updated)
    receipt = MigrationReceiptStore(
        {**_receipt("prepared"), "journal": json.loads(json.dumps(journal))}
    )

    restarted = _service(registry_store, backend)
    await restarted.async_load()
    assert await ManagedSwitchMigration(restarted, receipt).async_apply() == "completed"

    assert tuple(backend.updated) == updates_before_restart
    assert len(registry_store.registry.scenarios) == 63
    assert sum(item.enabled for item in registry_store.registry.scenarios) == 22
    assert receipt.value["state"] == "completed"


async def test_prepared_restart_after_registry_write_before_confirmation_is_adopted() -> None:
    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    entries = _entries_with_sources()
    before = await service.async_capture_managed_switch_migration(
        entries
    )
    journal = _initial_journal(before, entries)

    async def persist_operations(operations):
        journal["operations"] = json.loads(json.dumps(operations))

    async def persist_registry(state):
        journal["registry"] = json.loads(json.dumps(state))

    await service.async_stage_managed_switch_migration(
        entries,
        journal=journal,
        on_staged=persist_operations,
    )
    await service.async_apply_managed_switch_migration(
        entries,
        journal=journal,
        on_registry=persist_registry,
    )
    assert len(registry_store.registry.scenarios) == 63
    assert sum(item.enabled for item in registry_store.registry.scenarios) == 17
    journal["registry"]["state"] = "intent"
    updates_before_restart = tuple(backend.updated)
    writes_before_restart = len(registry_store.saved)
    receipt = MigrationReceiptStore(
        {**_receipt("prepared"), "journal": json.loads(json.dumps(journal))}
    )

    restarted = _service(registry_store, backend)
    await restarted.async_load()
    assert await ManagedSwitchMigration(restarted, receipt).async_apply() == "completed"

    assert tuple(backend.updated) == updates_before_restart
    assert len(registry_store.saved) == writes_before_restart + 1
    assert sum(item.enabled for item in registry_store.registry.scenarios) == 22
    assert receipt.value["state"] == "completed"


async def test_prepared_restart_after_finalize_completes_without_registry_rewrite() -> None:
    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    entries = _entries_with_sources()
    before = await service.async_capture_managed_switch_migration(
        entries
    )
    journal = _initial_journal(before, entries)

    async def persist_operations(operations):
        journal["operations"] = json.loads(json.dumps(operations))

    async def persist_registry(state):
        journal["registry"] = json.loads(json.dumps(state))

    await service.async_stage_managed_switch_migration(
        entries,
        journal=journal,
        on_staged=persist_operations,
    )
    await service.async_apply_managed_switch_migration(
        entries,
        journal=journal,
        on_registry=persist_registry,
    )
    await service.async_finalize_managed_switch_migration(
        entries,
        journal=journal,
        on_registry=persist_registry,
    )
    assert sum(item.enabled for item in registry_store.registry.scenarios) == 22
    updates_before_restart = tuple(backend.updated)
    writes_before_restart = len(registry_store.saved)
    receipt = MigrationReceiptStore(
        {**_receipt("prepared"), "journal": json.loads(json.dumps(journal))}
    )

    restarted = _service(registry_store, backend)
    await restarted.async_load()
    assert await ManagedSwitchMigration(restarted, receipt).async_apply() == "completed"

    assert tuple(backend.updated) == updates_before_restart
    assert len(registry_store.saved) == writes_before_restart
    assert receipt.value["state"] == "completed"
    assert backend.commits == 1


async def test_completed_restart_before_commit_finishes_backend_commit() -> None:
    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    entries = _entries_with_sources()
    before = await service.async_capture_managed_switch_migration(
        entries
    )
    journal = _initial_journal(before, entries)

    async def persist_operations(operations):
        journal["operations"] = json.loads(json.dumps(operations))

    async def persist_registry(state):
        journal["registry"] = json.loads(json.dumps(state))

    await service.async_stage_managed_switch_migration(
        entries,
        journal=journal,
        on_staged=persist_operations,
    )
    await service.async_apply_managed_switch_migration(
        entries,
        journal=journal,
        on_registry=persist_registry,
    )
    await service.async_finalize_managed_switch_migration(
        entries,
        journal=journal,
        on_registry=persist_registry,
    )
    journal["after"] = await service.async_capture_managed_switch_migration(
        entries
    )
    receipt = MigrationReceiptStore(
        {**_receipt("completed"), "journal": json.loads(json.dumps(journal))}
    )
    writes_before_restart = len(registry_store.saved)
    assert backend.commits == 0

    restarted = _service(registry_store, backend)
    await restarted.async_load()
    assert await ManagedSwitchMigration(restarted, receipt).async_apply() == "completed"

    assert backend.commits == 1
    assert len(registry_store.saved) == writes_before_restart
    assert receipt.saved == []


async def test_completed_replay_detects_drift_in_unaffected_registry_entry() -> None:
    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    receipt = MigrationReceiptStore(None)
    await ManagedSwitchMigration(service, receipt).async_apply()
    preserved = next(
        item for item in registry_store.registry.scenarios
        if item.id in _EXPECTED_PRESERVE_IDS
    )
    registry_store.registry = ScenarioRegistry(
        scenarios=tuple(
            replace(item, favorite=not item.favorite)
            if item.id == preserved.id
            else item
            for item in registry_store.registry.scenarios
        )
    )
    restarted = _service(registry_store, backend)
    await restarted.async_load()

    with unittest.TestCase().assertRaisesRegex(
        ManagedSwitchMigrationConflict, "completed image drifted"
    ):
        await ManagedSwitchMigration(restarted, receipt).async_apply()


async def test_staging_drains_affected_run_and_blocks_overlap_only() -> None:
    class Executor:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        def new_run_id(self):
            return "run-test"

        async def async_execute(self, _definition, _run_id, *, scenario_id, **_kwargs):
            if scenario_id == "system-bathroom-fan-off-night":
                self.started.set()
                await asyncio.Event().wait()
            return {"status": "completed", "receipts": []}

    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    executor = Executor()
    service = _service(registry_store, backend)
    service._executor = executor
    await service.async_load()
    running = asyncio.create_task(
        service.async_run_scenario("system-bathroom-fan-off-night")
    )
    await executor.started.wait()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)

    await service.async_stage_managed_switch_migration(
        MIGRATION_MANIFEST, journal=journal
    )
    with unittest.TestCase().assertRaises(asyncio.CancelledError):
        await running
    with unittest.TestCase().assertRaisesRegex(
        ScenarioServiceError, "closed during migration"
    ):
        await service.async_run_scenario("system-bathroom-fan-off-night")

    unrelated = next(
        item
        for item in registry_store.registry.scenarios
        if item.enabled
        and item.id not in service._managed_switch_blocked_runs
    )
    result = await service.async_run_scenario(unrelated.id)
    assert result["status"] == "completed"


async def test_staging_drains_nested_affected_run_before_source_changes() -> None:
    nested_id = "system-bathroom-fan-off-night"
    registry_store = RegistryStore(_registry_from_inventory58())
    nested_started = asyncio.Event()
    nested_finished = asyncio.Event()

    class Executor:
        service = None

        def new_run_id(self):
            return "run-nested"

        async def async_execute(
            self, _definition, _run_id, *, scenario_id, **_kwargs
        ):
            if scenario_id != nested_id:
                return await self.service.async_run_scenario(nested_id)
            nested_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                nested_finished.set()

    class QuiescenceBackend(Backend):
        async def async_prepare_release_source(self, *args, **kwargs):
            assert nested_finished.is_set()
            return await super().async_prepare_release_source(*args, **kwargs)

        async def async_prepare_new_release_source(self, *args, **kwargs):
            assert nested_finished.is_set()
            return await super().async_prepare_new_release_source(*args, **kwargs)

    backend = QuiescenceBackend()
    service = _service(registry_store, backend)
    executor = Executor()
    executor.service = service
    service._executor = executor
    await service.async_load()
    parent = next(
        item
        for item in registry_store.registry.scenarios
        if item.enabled and item.id in _EXPECTED_PRESERVE_IDS
    )
    running = asyncio.create_task(service.async_run_scenario(parent.id))
    await nested_started.wait()
    before = await service.async_capture_managed_switch_migration(
        MIGRATION_MANIFEST
    )
    journal = _initial_journal(before, MIGRATION_MANIFEST)

    await service.async_stage_managed_switch_migration(
        MIGRATION_MANIFEST,
        journal=journal,
    )

    with unittest.TestCase().assertRaises(asyncio.CancelledError):
        await running
    assert nested_finished.is_set()
    assert backend.updated


async def test_disabled_scenarios_reject_explicit_and_nested_calls_after_open() -> None:
    registry_store = RegistryStore(_registry_from_inventory58())
    backend = Backend()
    service = _service(registry_store, backend)
    await service.async_load()
    receipt = MigrationReceiptStore(None)
    await ManagedSwitchMigration(service, receipt).async_apply()
    assert service._managed_switch_blocked_runs == set()
    disabled_id = "system-bathroom-fan-off-night"

    class Executor:
        service = None
        device_calls = 0

        def new_run_id(self):
            return "run-disabled"

        async def async_execute(
            self, _definition, _run_id, *, scenario_id, **_kwargs
        ):
            if scenario_id != disabled_id:
                return await self.service.async_run_scenario(disabled_id)
            self.device_calls += 1
            return {"status": "completed", "receipts": []}

    executor = Executor()
    executor.service = service
    service._executor = executor
    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "disabled"):
        await service.async_run_scenario(disabled_id)
    parent = next(
        item
        for item in registry_store.registry.scenarios
        if item.enabled and item.id in _EXPECTED_PRESERVE_IDS
    )
    with unittest.TestCase().assertRaisesRegex(ScenarioServiceError, "disabled"):
        await service.async_run_scenario(parent.id)
    assert executor.device_calls == 0


def _as_unittest_case(test):
    @wraps(test)
    async def run() -> None:
        await test()

    return staticmethod(run)


class ManagedSwitchMigrationServiceTest(unittest.IsolatedAsyncioTestCase):
    """Expose the async migration cases to unittest and pytest alike."""

    test_batch_migration_updates_three_sources_and_registry_once = _as_unittest_case(
        test_batch_migration_updates_three_sources_and_registry_once
    )
    test_replace_source_preparation_runs_outside_registry_lock = _as_unittest_case(
        test_replace_source_preparation_runs_outside_registry_lock
    )
    test_replace_source_staging_restores_exact_source_after_cas_conflict = _as_unittest_case(
        test_replace_source_staging_restores_exact_source_after_cas_conflict
    )
    test_staged_flows_are_recovered_when_the_registry_cas_changes = _as_unittest_case(
        test_staged_flows_are_recovered_when_the_registry_cas_changes
    )
    test_prepared_restart_rejects_noncanonical_legacy_inputs = _as_unittest_case(
        test_prepared_restart_rejects_noncanonical_legacy_inputs
    )
    test_permuted_legacy_inputs_with_wrong_member_fail_before_mutation = _as_unittest_case(
        test_permuted_legacy_inputs_with_wrong_member_fail_before_mutation
    )
    test_permuted_migrated_inputs_remain_a_strict_conflict = _as_unittest_case(
        test_permuted_migrated_inputs_remain_a_strict_conflict
    )
    test_snapshot_binding_is_not_replayed_after_the_v4_migration = _as_unittest_case(
        test_snapshot_binding_is_not_replayed_after_the_v4_migration
    )
    test_each_replace_crash_point_reconciles_without_redeploy = _as_unittest_case(
        test_each_replace_crash_point_reconciles_without_redeploy
    )
    test_completed_registry_is_verified_without_any_mutation = _as_unittest_case(
        test_completed_registry_is_verified_without_any_mutation
    )
    test_final_verification_requires_one_cross_scenario_cas_snapshot = _as_unittest_case(
        test_final_verification_requires_one_cross_scenario_cas_snapshot
    )
    test_conflict_or_missing_target_causes_no_mutation = _as_unittest_case(
        test_conflict_or_missing_target_causes_no_mutation
    )
    test_topology_or_non_protected_scenario_conflict_causes_no_mutation = _as_unittest_case(
        test_topology_or_non_protected_scenario_conflict_causes_no_mutation
    )
    test_duplicate_manifest_entry_is_rejected_before_any_mutation = _as_unittest_case(
        test_duplicate_manifest_entry_is_rejected_before_any_mutation
    )
    test_registry_failure_compensates_sources_and_later_manual_edit_blocks_rollback = _as_unittest_case(
        test_registry_failure_compensates_sources_and_later_manual_edit_blocks_rollback
    )
    test_cancellation_after_first_source_mutation_restores_exact_source = _as_unittest_case(
        test_cancellation_after_first_source_mutation_restores_exact_source
    )
    test_final_snapshot_drift_can_restore_exact_sources_and_registry = _as_unittest_case(
        test_final_snapshot_drift_can_restore_exact_sources_and_registry
    )
    test_manual_source_edit_rejects_final_rollback_without_overwrite = _as_unittest_case(
        test_manual_source_edit_rejects_final_rollback_without_overwrite
    )
    test_completed_receipt_failure_with_manual_edit_stays_prepared_and_blocked = _as_unittest_case(
        test_completed_receipt_failure_with_manual_edit_stays_prepared_and_blocked
    )
    test_replace_intent_contains_exact_rollback_source_before_write = _as_unittest_case(
        test_replace_intent_contains_exact_rollback_source_before_write
    )
    test_exact_foreign_create_without_durable_intent_is_not_adopted_or_deleted = _as_unittest_case(
        test_exact_foreign_create_without_durable_intent_is_not_adopted_or_deleted
    )
    test_before_capture_rejects_existing_create_endpoint = _as_unittest_case(
        test_before_capture_rejects_existing_create_endpoint
    )
    test_prepared_create_intent_adopts_lost_response_and_keeps_original_before = _as_unittest_case(
        test_prepared_create_intent_adopts_lost_response_and_keeps_original_before
    )
    test_registry_write_has_durable_intent_and_lost_reply_is_reconciled = _as_unittest_case(
        test_registry_write_has_durable_intent_and_lost_reply_is_reconciled
    )
    test_cancellation_after_registry_write_rolls_back_exactly_and_can_retry = _as_unittest_case(
        test_cancellation_after_registry_write_rolls_back_exactly_and_can_retry
    )
    test_prepared_restart_before_registry_write_completes_exact_transition = _as_unittest_case(
        test_prepared_restart_before_registry_write_completes_exact_transition
    )
    test_prepared_restart_after_registry_write_before_confirmation_is_adopted = _as_unittest_case(
        test_prepared_restart_after_registry_write_before_confirmation_is_adopted
    )
    test_prepared_restart_after_finalize_completes_without_registry_rewrite = _as_unittest_case(
        test_prepared_restart_after_finalize_completes_without_registry_rewrite
    )
    test_completed_restart_before_commit_finishes_backend_commit = _as_unittest_case(
        test_completed_restart_before_commit_finishes_backend_commit
    )
    test_completed_replay_detects_drift_in_unaffected_registry_entry = _as_unittest_case(
        test_completed_replay_detects_drift_in_unaffected_registry_entry
    )
    test_full_startup_accepts_permuted_equivalent_baseline_without_reordering = _as_unittest_case(
        test_full_startup_accepts_permuted_equivalent_baseline_without_reordering
    )
    test_permuted_baseline_semantic_drift_fails_before_registry_or_source_write = _as_unittest_case(
        test_permuted_baseline_semantic_drift_fails_before_registry_or_source_write
    )
    test_staging_drains_affected_run_and_blocks_overlap_only = _as_unittest_case(
        test_staging_drains_affected_run_and_blocks_overlap_only
    )
    test_staging_drains_nested_affected_run_before_source_changes = _as_unittest_case(
        test_staging_drains_nested_affected_run_before_source_changes
    )
    test_disabled_scenarios_reject_explicit_and_nested_calls_after_open = _as_unittest_case(
        test_disabled_scenarios_reject_explicit_and_nested_calls_after_open
    )


for _test in (
    test_batch_migration_updates_three_sources_and_registry_once,
    test_replace_source_preparation_runs_outside_registry_lock,
    test_replace_source_staging_restores_exact_source_after_cas_conflict,
    test_staged_flows_are_recovered_when_the_registry_cas_changes,
    test_snapshot_binding_is_not_replayed_after_the_v4_migration,
    test_each_replace_crash_point_reconciles_without_redeploy,
    test_completed_registry_is_verified_without_any_mutation,
    test_final_verification_requires_one_cross_scenario_cas_snapshot,
    test_conflict_or_missing_target_causes_no_mutation,
    test_topology_or_non_protected_scenario_conflict_causes_no_mutation,
    test_duplicate_manifest_entry_is_rejected_before_any_mutation,
    test_registry_failure_compensates_sources_and_later_manual_edit_blocks_rollback,
    test_cancellation_after_first_source_mutation_restores_exact_source,
    test_final_snapshot_drift_can_restore_exact_sources_and_registry,
    test_manual_source_edit_rejects_final_rollback_without_overwrite,
    test_completed_receipt_failure_with_manual_edit_stays_prepared_and_blocked,
    test_replace_intent_contains_exact_rollback_source_before_write,
    test_exact_foreign_create_without_durable_intent_is_not_adopted_or_deleted,
    test_before_capture_rejects_existing_create_endpoint,
    test_prepared_create_intent_adopts_lost_response_and_keeps_original_before,
    test_registry_write_has_durable_intent_and_lost_reply_is_reconciled,
    test_cancellation_after_registry_write_rolls_back_exactly_and_can_retry,
    test_prepared_restart_before_registry_write_completes_exact_transition,
    test_prepared_restart_after_registry_write_before_confirmation_is_adopted,
    test_prepared_restart_after_finalize_completes_without_registry_rewrite,
    test_completed_restart_before_commit_finishes_backend_commit,
    test_completed_replay_detects_drift_in_unaffected_registry_entry,
    test_full_startup_accepts_permuted_equivalent_baseline_without_reordering,
    test_permuted_baseline_semantic_drift_fails_before_registry_or_source_write,
    test_staging_drains_affected_run_and_blocks_overlap_only,
    test_staging_drains_nested_affected_run_before_source_changes,
    test_disabled_scenarios_reject_explicit_and_nested_calls_after_open,
):
    _test.__test__ = False
