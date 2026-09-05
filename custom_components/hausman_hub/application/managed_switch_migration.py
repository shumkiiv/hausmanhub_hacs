"""Verified, idempotent migration gate for release-owned switch scenarios."""
from __future__ import annotations

from collections.abc import Mapping
import inspect

MIGRATION_ID = "managed-switches"
MIGRATION_VERSION = 1
LEGACY_MANAGED_SWITCHES = {
    "system-shower-comfort-controller": (3, "4ecf6735e3350c89116c9e1ec56f649fc9c6ba420ca884dcd43347bbc8bb3257"),
    "system-small-corridor-light-controller": (1, "ce2580a1a8616b313b832d4da4c7648c4d01e5ce6b65d1fabf0ae1ac15672a44"),
    "system-tambur-adaptive-controller": (7, "0551ee02fc052a99a2e802054b8aaeaa1ada5885b927b90eb3cc8d2aca3414f9"),
}


class ManagedSwitchMigrationConflict(RuntimeError):
    pass


class ManagedSwitchMigration:
    def __init__(self, service: object, store: object) -> None:
        self._service = service
        self._store = store

    async def async_apply(self) -> str:
        receipt = await self._store.async_load()
        if isinstance(receipt, Mapping) and receipt.get("state") == "completed":
            return "completed"
        for scenario_id, (revision, source_hash) in LEGACY_MANAGED_SWITCHES.items():
            scenario = self._service.async_get_scenario(scenario_id)
            if inspect.isawaitable(scenario):
                scenario = await scenario
            if not getattr(scenario, "protected", False):
                raise ManagedSwitchMigrationConflict(f"scenario {scenario_id} is not protected")
            metadata = getattr(getattr(scenario, "definition", None), "node_red", None)
            if getattr(scenario, "revision", None) != revision or getattr(metadata, "source_hash", None) != source_hash:
                raise ManagedSwitchMigrationConflict(f"scenario {scenario_id} changed outside migration")
        await self._store.async_save({"migrationId": MIGRATION_ID, "version": MIGRATION_VERSION, "state": "completed"})
        return "completed"
