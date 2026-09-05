from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.managed_switch_migration import (
    LEGACY_MANAGED_SWITCHES,
    ManagedSwitchMigration,
    ManagedSwitchMigrationConflict,
)


class Store:
    def __init__(self, value=None): self.value = value; self.saved = []
    async def async_load(self): return self.value
    async def async_save(self, value): self.saved.append(value); self.value = value


def test_migration_rejects_legacy_revision_or_hash_conflict_without_save() -> None:
    store = Store()
    service = SimpleNamespace(async_get_scenario=lambda _id: SimpleNamespace(protected=True, revision=99, definition=SimpleNamespace(node_red=SimpleNamespace(source_hash="changed"))))
    migration = ManagedSwitchMigration(service, store)
    with pytest.raises(ManagedSwitchMigrationConflict):
        asyncio.run(migration.async_apply())
    assert store.saved == []


def test_completed_receipt_is_idempotent() -> None:
    store = Store({"migrationId": "managed-switches", "version": 1, "state": "completed"})
    service = SimpleNamespace()
    assert asyncio.run(ManagedSwitchMigration(service, store).async_apply()) == "completed"
    assert store.saved == []


def test_manifest_contains_exact_three_protected_scenarios() -> None:
    assert set(LEGACY_MANAGED_SWITCHES) == {
        "system-shower-comfort-controller",
        "system-small-corridor-light-controller",
        "system-tambur-adaptive-controller",
    }
