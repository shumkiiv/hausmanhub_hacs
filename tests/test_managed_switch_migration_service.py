from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.managed_switch_migration import MIGRATION_MANIFEST
from custom_components.hausman_hub.application.scenario_node_red import NodeRedSourceConflict
from custom_components.hausman_hub.application.scenario_service import (
    ScenarioRevisionConflictError,
    ScenarioService,
    ScenarioServiceError,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioAction,
    ScenarioActionType,
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


def _registry(*, migrated: set[str] = frozenset()) -> ScenarioRegistry:
    scenarios = []
    for entry in MIGRATION_MANIFEST:
        done = entry.scenario_id in migrated
        metadata = ScenarioNodeRedMetadata(
            flow_id=f"flow-{entry.scenario_id}", flow_revision=8,
            source_hash=entry.new_source_hash if done else entry.legacy_source_hash,
            generated_by=ScenarioNodeRedGeneratedBy.HAUSMAN,
            sync_status=ScenarioNodeRedSyncStatus.SYNCED,
            input_target_ids=entry.input_target_ids if done else entry.legacy_input_target_ids,
        )
        definition = ScenarioDefinition(
            version=1, execution_mode=ScenarioExecutionMode.RESTART,
            execution_backend=ScenarioExecutionBackend.NODE_RED, node_red=metadata,
            triggers=(ScenarioTrigger("manual", ScenarioTriggerType.MANUAL),),
            conditions=(),
            actions=(ScenarioAction("notify", ScenarioActionType.NOTIFICATION, message="ok"),),
        )
        scenarios.append(Scenario.from_definition(
            entry.scenario_id, entry.scenario_id, definition, group="system",
            revision=entry.legacy_revision + (1 if done else 0), protected=True,
        ))
    return ScenarioRegistry(scenarios=tuple(scenarios))


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
            item.scenario_id: item.legacy_source_hash for item in MIGRATION_MANIFEST
        }
        self.updated = []
        self.restored = []

    async def async_verify_managed_topology(self, scenario_id, _flow_id):
        return {"source_hash": self.deployed[scenario_id], "topology": "managed-three-node-v1"}

    async def async_update_source(self, scenario_id, _definition, _flow_id, source, expected, _catalog, *, validate_only):
        assert not validate_only and self.deployed[scenario_id] == expected
        entry = next(item for item in MIGRATION_MANIFEST if item.scenario_id == scenario_id)
        self.deployed[scenario_id] = entry.new_source_hash
        self.updated.append(scenario_id)
        return {"saved": True, "proposed_source_hash": entry.new_source_hash, "previous_source": f"legacy:{scenario_id}"}

    async def async_restore_source(self, scenario_id, _flow_id, _source, *, expected_current_hash):
        if self.deployed[scenario_id] != expected_current_hash:
            raise NodeRedSourceConflict(expected_current_hash, self.deployed[scenario_id])
        entry = next(item for item in MIGRATION_MANIFEST if item.scenario_id == scenario_id)
        self.deployed[scenario_id] = entry.legacy_source_hash
        self.restored.append(scenario_id)


def _service(store, backend, *, missing=None):
    targets = {target for item in MIGRATION_MANIFEST for target in item.input_target_ids}
    catalog = SimpleNamespace(
        devices=tuple(targets),
        device=lambda target: None if target == missing else SimpleNamespace(target_id=target),
    )
    return ScenarioService(None, store, catalog, node_red_backend=backend)


@pytest.mark.asyncio
async def test_batch_migration_updates_three_sources_and_registry_once() -> None:
    store = RegistryStore(_registry())
    backend = Backend()
    service = _service(store, backend)
    await service.async_load()
    assert await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST) == "completed"
    assert set(backend.updated) == {item.scenario_id for item in MIGRATION_MANIFEST}
    assert len(store.saved) == 1
    for entry in MIGRATION_MANIFEST:
        scenario = store.registry.scenario(entry.scenario_id)
        assert scenario.revision == entry.legacy_revision + 1
        assert scenario.definition.node_red.source_hash == entry.new_source_hash
        assert scenario.definition.node_red.input_target_ids == entry.input_target_ids


@pytest.mark.asyncio
async def test_partial_restart_reconciles_new_source_without_redeploy() -> None:
    partial = MIGRATION_MANIFEST[0]
    backend = Backend({
        item.scenario_id: partial.new_source_hash if item is partial else item.legacy_source_hash
        for item in MIGRATION_MANIFEST
    })
    store = RegistryStore(_registry())
    service = _service(store, backend)
    await service.async_load()
    await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert partial.scenario_id not in backend.updated
    migrated = store.registry.scenario(partial.scenario_id)
    assert migrated.revision == partial.legacy_revision + 1
    assert migrated.definition.node_red.flow_revision == 9


@pytest.mark.asyncio
async def test_completed_registry_is_verified_without_any_mutation() -> None:
    migrated = {item.scenario_id for item in MIGRATION_MANIFEST}
    backend = Backend({item.scenario_id: item.new_source_hash for item in MIGRATION_MANIFEST})
    store = RegistryStore(_registry(migrated=migrated))
    service = _service(store, backend)
    await service.async_load()
    assert await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST) == "completed"
    assert backend.updated == []
    assert store.saved == []


@pytest.mark.asyncio
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
    with pytest.raises(ScenarioRevisionConflictError):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []

    backend = Backend()
    backend.deployed[MIGRATION_MANIFEST[0].scenario_id] = "a" * 64
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()
    with pytest.raises(ScenarioRevisionConflictError):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []

    backend = Backend()
    service = _service(RegistryStore(_registry()), backend, missing=MIGRATION_MANIFEST[0].input_target_ids[0])
    await service.async_load()
    with pytest.raises(ScenarioServiceError, match="target is missing"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []


@pytest.mark.asyncio
async def test_topology_or_non_protected_scenario_conflict_causes_no_mutation() -> None:
    class WrongTopology(Backend):
        async def async_verify_managed_topology(self, scenario_id, flow_id):
            evidence = await super().async_verify_managed_topology(scenario_id, flow_id)
            evidence["topology"] = "rerouted"
            return evidence

    backend = WrongTopology()
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()
    with pytest.raises(ScenarioServiceError, match="topology changed"):
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
    with pytest.raises(ScenarioServiceError, match="protected by system policy"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert backend.updated == []


@pytest.mark.asyncio
async def test_duplicate_manifest_entry_is_rejected_before_any_mutation() -> None:
    backend = Backend()
    service = _service(RegistryStore(_registry()), backend)
    await service.async_load()

    with pytest.raises(ScenarioServiceError, match="manifest is invalid"):
        await service.async_apply_managed_switch_migration(
            (*MIGRATION_MANIFEST, MIGRATION_MANIFEST[0])
        )

    assert backend.updated == []


@pytest.mark.asyncio
async def test_registry_failure_compensates_sources_and_later_manual_edit_blocks_rollback() -> None:
    backend = Backend()
    store = RegistryStore(_registry(), fail=True)
    service = _service(store, backend)
    await service.async_load()
    with pytest.raises(ScenarioServiceError, match="write failed"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
    assert set(backend.restored) == {item.scenario_id for item in MIGRATION_MANIFEST}

    backend = Backend()
    first = MIGRATION_MANIFEST[0]
    store = RegistryStore(
        _registry(), fail=True,
        before_fail=lambda: backend.deployed.__setitem__(first.scenario_id, "f" * 64),
    )
    service = _service(store, backend)
    await service.async_load()
    with pytest.raises(ScenarioServiceError, match="CAS rollback was rejected"):
        await service.async_apply_managed_switch_migration(MIGRATION_MANIFEST)
