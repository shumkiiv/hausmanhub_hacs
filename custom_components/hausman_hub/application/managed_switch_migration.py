"""Verified, restart-safe migration for release-owned switch scenarios."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

MIGRATION_ID = "managed-switches"
MIGRATION_VERSION = 2
MANAGED_TOPOLOGY = "managed-three-node-v1"


@dataclass(frozen=True, slots=True)
class ManagedSwitchMigrationEntry:
    scenario_id: str
    legacy_revision: int
    legacy_source_hash: str
    legacy_topology: str
    legacy_input_target_ids: tuple[str, ...]
    input_target_ids: tuple[str, ...]
    new_source_hash: str
    source_file: str
    source: str = ""


_SHOWER_LEGACY_INPUTS = (
    "entity_d1fb2cbf2a691bba", "entity_fd3945cf1a2110f8",
    "entity_6b9ccdab9bb484b2", "entity_4be32416634e6416",
    "entity_1fdcd8b244637246", "entity_afef5df0e0cae309",
    "entity_e7a7c61eec7bdff8",
)
_SHOWER_INPUTS = tuple(
    "entity_46174e1ff9913212" if item == "entity_4be32416634e6416" else item
    for item in _SHOWER_LEGACY_INPUTS
)
_SMALL_LEGACY_INPUTS = (
    "entity_90417aada6a33491", "entity_6b9ccdab9bb484b2",
    "entity_5f3b4436fb7b6f2b", "entity_c9d6bc67f172f30d",
    "entity_ff0244d6b760be7e", "entity_9ed909332fdaa8fd",
)
_SMALL_INPUTS = tuple(
    "entity_4be32416634e6416" if item == "entity_ff0244d6b760be7e" else item
    for item in _SMALL_LEGACY_INPUTS
)
_TAMBUR_INPUTS = (
    "entity_156050daca86aa6c", "entity_10b78187426f8485",
    "entity_6b9ccdab9bb484b2", "entity_5f3b4436fb7b6f2b",
    "entity_71859313239a14e4", "entity_cd0098e5ff95da46",
    "entity_fbdf27871edb89bf", "entity_b47991988cc6b9f3",
    "entity_170c7a4e2505b803",
)

MIGRATION_MANIFEST: tuple[ManagedSwitchMigrationEntry, ...] = (
    ManagedSwitchMigrationEntry(
        "system-shower-comfort-controller", 3,
        "4ecf6735e3350c89116c9e1ec56f649fc9c6ba420ca884dcd43347bbc8bb3257",
        MANAGED_TOPOLOGY, _SHOWER_LEGACY_INPUTS, _SHOWER_INPUTS,
        "757bde711c85ebad4826c2ec0bf2695d0034f7dd820c9ec7c30816f3f37c1551",
        "shower_controller.js",
    ),
    ManagedSwitchMigrationEntry(
        "system-small-corridor-light-controller", 1,
        "ce2580a1a8616b313b832d4da4c7648c4d01e5ce6b65d1fabf0ae1ac15672a44",
        MANAGED_TOPOLOGY, _SMALL_LEGACY_INPUTS, _SMALL_INPUTS,
        "bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1",
        "small_corridor_controller.js",
    ),
    ManagedSwitchMigrationEntry(
        "system-tambur-adaptive-controller", 7,
        "0551ee02fc052a99a2e802054b8aaeaa1ada5885b927b90eb3cc8d2aca3414f9",
        MANAGED_TOPOLOGY, _TAMBUR_INPUTS, _TAMBUR_INPUTS,
        "4daef9ac2de8dc1c95dd2da6887e178751a65d0e47bcf48443635f68eb1ba5dc",
        "tambur_controller.js",
    ),
)
LEGACY_MANAGED_SWITCHES = {
    item.scenario_id: (item.legacy_revision, item.legacy_source_hash)
    for item in MIGRATION_MANIFEST
}


class ManagedSwitchMigrationConflict(RuntimeError):
    """The durable receipt or live CAS evidence does not match the manifest."""


def _manifest_hash() -> str:
    payload = [
        {
            "scenarioId": item.scenario_id,
            "legacyRevision": item.legacy_revision,
            "legacySourceHash": item.legacy_source_hash,
            "legacyTopology": item.legacy_topology,
            "legacyInputs": item.legacy_input_target_ids,
            "inputs": item.input_target_ids,
            "newSourceHash": item.new_source_hash,
            "sourceFile": item.source_file,
        }
        for item in MIGRATION_MANIFEST
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


MANIFEST_HASH = _manifest_hash()


def valid_managed_switch_migration_payload(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"migrationId", "version", "state", "manifestHash"}
        and value.get("migrationId") == MIGRATION_ID
        and value.get("version") == MIGRATION_VERSION
        and value.get("state") in {"prepared", "completed"}
        and value.get("manifestHash") == MANIFEST_HASH
    )


def _receipt(state: str) -> dict[str, object]:
    return {
        "migrationId": MIGRATION_ID,
        "version": MIGRATION_VERSION,
        "state": state,
        "manifestHash": MANIFEST_HASH,
    }


def _entries_with_sources() -> tuple[ManagedSwitchMigrationEntry, ...]:
    root = Path(__file__).resolve().parents[1] / "managed_scenarios"
    entries: list[ManagedSwitchMigrationEntry] = []
    for item in MIGRATION_MANIFEST:
        source = (root / item.source_file).read_text(encoding="utf-8")
        if hashlib.sha256(source.encode()).hexdigest() != item.new_source_hash:
            raise ManagedSwitchMigrationConflict("release-owned source hash mismatch")
        entries.append(
            ManagedSwitchMigrationEntry(
                item.scenario_id, item.legacy_revision, item.legacy_source_hash,
                item.legacy_topology, item.legacy_input_target_ids,
                item.input_target_ids, item.new_source_hash, item.source_file, source,
            )
        )
    return tuple(entries)


class HomeAssistantManagedSwitchMigrationStore:
    """Verified atomic HA storage for the migration receipt."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415
        from ..verified_safety_storage import VerifiedSafetyStore  # noqa: PLC0415

        store: Store[dict[str, object]] = Store(
            hass, 1, f"hausman_hub.managed_switch_migration.{entry_id}", atomic_writes=True
        )
        self._store = VerifiedSafetyStore(
            store, hass.async_add_executor_job,
            payload_validator=valid_managed_switch_migration_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


class ManagedSwitchMigration:
    def __init__(self, service: object, store: object) -> None:
        self._service = service
        self._store = store

    async def async_apply(self) -> str:
        loaded = await self._store.async_load()
        if loaded is not None and not valid_managed_switch_migration_payload(loaded):
            raise ManagedSwitchMigrationConflict("migration receipt is invalid")
        completed = isinstance(loaded, Mapping) and loaded.get("state") == "completed"
        if loaded is None:
            await self._store.async_save(_receipt("prepared"))
        apply = getattr(self._service, "async_apply_managed_switch_migration", None)
        if not callable(apply):
            raise ManagedSwitchMigrationConflict("scenario migration CAS is unavailable")
        await apply(_entries_with_sources())
        if not completed:
            await self._store.async_save(_receipt("completed"))
        return "completed"
