from __future__ import annotations

import asyncio

import pytest

from custom_components.hausman_hub.application.managed_switch_migration import (
    LEGACY_MANAGED_SWITCHES,
    MANIFEST_HASH,
    MIGRATION_MANIFEST,
    ManagedSwitchMigration,
    ManagedSwitchMigrationConflict,
    valid_managed_switch_migration_payload,
)


class Store:
    def __init__(self, value=None, *, fail_save=False):
        self.value = value
        self.saved = []
        self.fail_save = fail_save

    async def async_load(self):
        return self.value

    async def async_save(self, value):
        if self.fail_save:
            raise OSError("verified save failed")
        self.saved.append(value)
        self.value = value


class Service:
    def __init__(self, *, fail=False, verification_drift=None):
        self.calls = []
        self.fail = fail
        self.verification_drift = verification_drift
        self.verifications = 0

    async def async_apply_managed_switch_migration(self, entries):
        self.calls.append(entries)
        if self.fail:
            raise RuntimeError("CAS conflict")

    async def async_verify_managed_switch_migration(self, entries):
        self.verifications += 1
        if self.verification_drift is not None:
            raise RuntimeError(f"final drift: {self.verification_drift}")
        assert len(entries) == 3
        return "revision.final"


def test_manifest_contains_exact_three_protected_scenarios_and_sources() -> None:
    assert set(LEGACY_MANAGED_SWITCHES) == {
        "system-shower-comfort-controller",
        "system-small-corridor-light-controller",
        "system-tambur-adaptive-controller",
    }
    for item in MIGRATION_MANIFEST:
        assert item.legacy_topology == "managed-three-node-v1"
        assert len(item.legacy_source_hash) == len(item.new_source_hash) == 64
        assert item.input_target_ids


def test_migration_persists_prepared_before_cas_and_completed_after() -> None:
    store = Store()
    service = Service()
    assert asyncio.run(ManagedSwitchMigration(service, store).async_apply()) == "completed"
    assert [item["state"] for item in store.saved] == ["prepared", "completed"]
    assert all(valid_managed_switch_migration_payload(item) for item in store.saved)
    assert len(service.calls[0]) == 3
    assert all(item.source for item in service.calls[0])


def test_storage_failure_before_prepare_causes_no_mutation() -> None:
    store = Store(fail_save=True)
    service = Service()
    with pytest.raises(OSError, match="verified save failed"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())
    assert service.calls == []


def test_prepared_receipt_reconciles_and_completed_is_idempotent() -> None:
    prepared = {
        "migrationId": "managed-switches", "version": 2,
        "state": "prepared", "manifestHash": MANIFEST_HASH,
    }
    store = Store(prepared)
    service = Service()
    asyncio.run(ManagedSwitchMigration(service, store).async_apply())
    assert len(service.calls) == 1
    completed = Store(store.value)
    second = Service()
    assert asyncio.run(ManagedSwitchMigration(second, completed).async_apply()) == "completed"
    assert len(second.calls) == 1
    assert completed.saved == []


def test_invalid_or_foreign_receipt_fails_closed() -> None:
    store = Store({"migrationId": "managed-switches", "version": 2, "state": "completed", "manifestHash": "0" * 64})
    with pytest.raises(ManagedSwitchMigrationConflict, match="receipt"):
        asyncio.run(ManagedSwitchMigration(Service(), store).async_apply())


def test_cas_conflict_leaves_prepared_receipt_for_restart_reconciliation() -> None:
    store = Store()
    with pytest.raises(RuntimeError, match="CAS conflict"):
        asyncio.run(ManagedSwitchMigration(Service(fail=True), store).async_apply())
    assert store.value["state"] == "prepared"


@pytest.mark.parametrize(
    "scenario_id",
    [item.scenario_id for item in MIGRATION_MANIFEST],
)
def test_final_cross_scenario_drift_never_completes_receipt(
    scenario_id: str,
) -> None:
    store = Store()
    service = Service(verification_drift=scenario_id)

    with pytest.raises(RuntimeError, match="final drift"):
        asyncio.run(ManagedSwitchMigration(service, store).async_apply())

    assert service.verifications == 1
    assert store.value["state"] == "prepared"
