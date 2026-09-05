"""Verified second phase for the small-corridor manual switch binding."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING

from .managed_switch_migration import MANAGED_TOPOLOGY, MIGRATION_MANIFEST

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BINDING_MIGRATION_ID = "managed-switch-bindings"
BINDING_MIGRATION_VERSION = 1
SMALL_CORRIDOR_SCENARIO_ID = "system-small-corridor-light-controller"
SMALL_CORRIDOR_SOURCE_HASH = (
    "bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1"
)
SMALL_CORRIDOR_OLD_TRIGGER_TARGET = "entity_ff0244d6b760be7e"
SMALL_CORRIDOR_NEW_TRIGGER_TARGET = "entity_4be32416634e6416"
SMALL_CORRIDOR_NEW_TRIGGER_TARGET_NAME = "Выключатель малого коридора"


@dataclass(frozen=True, slots=True)
class ManagedSwitchBindingEntry:
    scenario_id: str
    expected_revision: int
    source_hash: str
    topology: str
    input_target_ids: tuple[str, ...]
    trigger_id: str
    old_target_id: str
    new_target_id: str
    new_target_name: str


_SMALL_CORRIDOR_INPUTS = next(
    item.input_target_ids
    for item in MIGRATION_MANIFEST
    if item.scenario_id == SMALL_CORRIDOR_SCENARIO_ID
)

BINDING_MIGRATION_MANIFEST = (
    ManagedSwitchBindingEntry(
        scenario_id=SMALL_CORRIDOR_SCENARIO_ID,
        expected_revision=2,
        source_hash=SMALL_CORRIDOR_SOURCE_HASH,
        topology=MANAGED_TOPOLOGY,
        input_target_ids=_SMALL_CORRIDOR_INPUTS,
        trigger_id="manual_chandelier_on",
        old_target_id=SMALL_CORRIDOR_OLD_TRIGGER_TARGET,
        new_target_id=SMALL_CORRIDOR_NEW_TRIGGER_TARGET,
        new_target_name=SMALL_CORRIDOR_NEW_TRIGGER_TARGET_NAME,
    ),
)


def _manifest_hash() -> str:
    payload = [
        {
            "scenarioId": item.scenario_id,
            "expectedRevision": item.expected_revision,
            "sourceHash": item.source_hash,
            "topology": item.topology,
            "inputs": item.input_target_ids,
            "triggerId": item.trigger_id,
            "oldTargetId": item.old_target_id,
            "newTargetId": item.new_target_id,
            "newTargetName": item.new_target_name,
        }
        for item in BINDING_MIGRATION_MANIFEST
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


BINDING_MANIFEST_HASH = _manifest_hash()


def valid_managed_switch_binding_payload(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"migrationId", "version", "state", "manifestHash"}
        and value.get("migrationId") == BINDING_MIGRATION_ID
        and value.get("version") == BINDING_MIGRATION_VERSION
        and value.get("state") in {"prepared", "completed"}
        and value.get("manifestHash") == BINDING_MANIFEST_HASH
    )


def _receipt(state: str) -> dict[str, object]:
    return {
        "migrationId": BINDING_MIGRATION_ID,
        "version": BINDING_MIGRATION_VERSION,
        "state": state,
        "manifestHash": BINDING_MANIFEST_HASH,
    }


class ManagedSwitchBindingMigrationConflict(RuntimeError):
    """The binding receipt or live CAS evidence is not release-owned."""


class HomeAssistantManagedSwitchBindingMigrationStore:
    """Separate verified receipt store for binding phase B."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415

        from ..verified_safety_storage import VerifiedSafetyStore  # noqa: PLC0415

        store: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.managed_switch_binding_migration.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            store,
            hass.async_add_executor_job,
            payload_validator=valid_managed_switch_binding_payload,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


class ManagedSwitchBindingMigration:
    """Apply and durably attest the binding-only CAS phase."""

    def __init__(self, service: object, store: object) -> None:
        self._service = service
        self._store = store

    async def async_apply(self) -> str:
        loaded = await self._store.async_load()
        if loaded is not None and not valid_managed_switch_binding_payload(loaded):
            raise ManagedSwitchBindingMigrationConflict(
                "binding migration receipt is invalid"
            )
        completed = isinstance(loaded, Mapping) and loaded.get("state") == "completed"
        if loaded is None:
            await self._store.async_save(_receipt("prepared"))

        apply = getattr(
            self._service,
            "async_apply_managed_switch_binding_migration",
            None,
        )
        if not callable(apply):
            raise ManagedSwitchBindingMigrationConflict(
                "binding migration CAS is unavailable"
            )
        entries = BINDING_MIGRATION_MANIFEST
        applied = False
        try:
            await apply(entries)
            applied = True
            verify = getattr(
                self._service,
                "async_verify_managed_switch_binding_migration",
                None,
            )
            if not callable(verify):
                raise ManagedSwitchBindingMigrationConflict(
                    "binding migration verification is unavailable"
                )
            await verify(entries)
            if not completed:
                await self._store.async_save(_receipt("completed"))
                await verify(entries)
            finalize = getattr(
                self._service,
                "async_finalize_managed_switch_binding_migration",
                None,
            )
            if not callable(finalize):
                raise ManagedSwitchBindingMigrationConflict(
                    "binding migration finalization is unavailable"
                )
            await finalize(entries)
        except (Exception, asyncio.CancelledError) as error:
            if not applied:
                raise
            try:
                await asyncio.shield(self._store.async_save(_receipt("prepared")))
            except Exception:  # noqa: BLE001
                pass
            rollback = getattr(
                self._service,
                "async_rollback_managed_switch_binding_migration",
                None,
            )
            rollback_complete = False
            if callable(rollback):
                try:
                    rollback_complete = await asyncio.shield(rollback(entries)) is True
                except Exception:  # noqa: BLE001
                    rollback_complete = False
            if not rollback_complete:
                raise ManagedSwitchBindingMigrationConflict(
                    "managed switch binding recovery is required"
                ) from error
            raise
        return "completed"
