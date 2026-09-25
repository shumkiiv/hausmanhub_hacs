"""Verified second phase for the small-corridor manual switch binding."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING

from .managed_switch_migration import MANAGED_TOPOLOGY

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BINDING_MIGRATION_ID = "managed-switch-bindings"
BINDING_MIGRATION_VERSION = 1
SMALL_CORRIDOR_SCENARIO_ID = "system-small-corridor-light-controller"
SMALL_CORRIDOR_SOURCE_HASH = (
    "718f13b5a6eb08f9a1eea3d5f844b841795d1089a4fa38e74fc08006b956ae0c"
)
SMALL_CORRIDOR_NEW_TRIGGER_TARGET_NAME = "Выключатель малого коридора"
SMALL_CORRIDOR_INPUT_TARGETS = (
    "protected_small_motion",
    "protected_shared_sun",
    "protected_small_lux",
    "protected_small_relay",
    "protected_small_chandelier",
)
SMALL_CORRIDOR_OLD_TRIGGER_TARGET = "protected_small_switch_old"
SMALL_CORRIDOR_NEW_TRIGGER_TARGET = "protected_small_switch"


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


BINDING_MIGRATION_MANIFEST: tuple[ManagedSwitchBindingEntry, ...] = (
    ManagedSwitchBindingEntry(
        scenario_id=SMALL_CORRIDOR_SCENARIO_ID,
        expected_revision=2,
        source_hash=SMALL_CORRIDOR_SOURCE_HASH,
        topology=MANAGED_TOPOLOGY,
        input_target_ids=SMALL_CORRIDOR_INPUT_TARGETS,
        trigger_id="manual_chandelier_on",
        old_target_id=SMALL_CORRIDOR_OLD_TRIGGER_TARGET,
        new_target_id=SMALL_CORRIDOR_NEW_TRIGGER_TARGET,
        new_target_name=SMALL_CORRIDOR_NEW_TRIGGER_TARGET_NAME,
    ),
)


def _manifest_hash(
    manifest: tuple[ManagedSwitchBindingEntry, ...] = BINDING_MIGRATION_MANIFEST,
) -> str:
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
        for item in manifest
    ]
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


BINDING_MANIFEST_HASH = _manifest_hash(BINDING_MIGRATION_MANIFEST)


def valid_managed_switch_binding_payload(
    value: object,
    manifest: tuple[ManagedSwitchBindingEntry, ...] = BINDING_MIGRATION_MANIFEST,
) -> bool:
    manifest_hash = _manifest_hash(manifest)
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"migrationId", "version", "state", "manifestHash"}
        and value.get("migrationId") == BINDING_MIGRATION_ID
        and value.get("version") == BINDING_MIGRATION_VERSION
        and value.get("state") in {"prepared", "completed"}
        and value.get("manifestHash") == manifest_hash
    )


def _receipt(
    state: str,
    manifest: tuple[ManagedSwitchBindingEntry, ...] = BINDING_MIGRATION_MANIFEST,
) -> dict[str, object]:
    return {
        "migrationId": BINDING_MIGRATION_ID,
        "version": BINDING_MIGRATION_VERSION,
        "state": state,
        "manifestHash": _manifest_hash(manifest),
    }


class ManagedSwitchBindingMigrationConflict(RuntimeError):
    """The binding receipt or live CAS evidence is not release-owned."""


class HomeAssistantManagedSwitchBindingMigrationStore:
    """Separate verified receipt store for binding phase B."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        *,
        manifest: tuple[ManagedSwitchBindingEntry, ...] = BINDING_MIGRATION_MANIFEST,
    ) -> None:
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
            payload_validator=lambda value: valid_managed_switch_binding_payload(
                value, manifest
            ),
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


class ManagedSwitchBindingMigration:
    """Apply and durably attest the binding-only CAS phase."""

    def __init__(
        self,
        service: object,
        store: object,
        *,
        manifest: tuple[ManagedSwitchBindingEntry, ...] = BINDING_MIGRATION_MANIFEST,
    ) -> None:
        self._service = service
        self._store = store
        self._manifest = manifest

    async def async_apply(self) -> str:
        loaded = await self._store.async_load()
        if loaded is not None and not valid_managed_switch_binding_payload(
            loaded, self._manifest
        ):
            raise ManagedSwitchBindingMigrationConflict(
                "binding migration receipt is invalid"
            )
        completed = isinstance(loaded, Mapping) and loaded.get("state") == "completed"
        if loaded is None:
            await self._store.async_save(_receipt("prepared", self._manifest))

        apply = getattr(
            self._service,
            "async_apply_managed_switch_binding_migration",
            None,
        )
        if not callable(apply):
            raise ManagedSwitchBindingMigrationConflict(
                "binding migration CAS is unavailable"
            )
        entries = self._manifest
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
                await self._store.async_save(_receipt("completed", self._manifest))
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
                await asyncio.shield(
                    self._store.async_save(_receipt("prepared", self._manifest))
                )
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


def managed_switch_binding_manifest_from_protected_topology(
    protected_topology: object,
) -> tuple[ManagedSwitchBindingEntry, ...]:
    """Resolve phase-B binding entries from protected roles."""

    expectations_getter = getattr(protected_topology, "migration_expectations", None)
    target_getter = getattr(protected_topology, "target_id", None)
    targets_getter = getattr(protected_topology, "targets_for_roles", None)
    if not (
        callable(expectations_getter)
        and callable(target_getter)
        and callable(targets_getter)
    ):
        raise ManagedSwitchBindingMigrationConflict(
            "protected binding topology is unavailable"
        )
    entries: list[ManagedSwitchBindingEntry] = []
    for item in expectations_getter("managedSwitchBindings"):
        if not isinstance(item, Mapping):
            raise ManagedSwitchBindingMigrationConflict(
                "protected binding topology is invalid"
            )
        input_roles = tuple(str(role) for role in item.get("inputRoles", ()))
        input_targets = targets_getter(input_roles)
        old_target = target_getter(str(item.get("oldTargetRole")))
        new_target = target_getter(str(item.get("newTargetRole")))
        if (
            len(input_targets) != len(input_roles)
            or not input_targets
            or not isinstance(old_target, str)
            or not old_target
            or not isinstance(new_target, str)
            or not new_target
        ):
            raise ManagedSwitchBindingMigrationConflict(
                "protected binding topology is incomplete"
            )
        entries.append(
            ManagedSwitchBindingEntry(
                scenario_id=str(item["scenarioId"]),
                expected_revision=int(item["expectedRevision"]),
                source_hash=str(item["sourceHash"]),
                topology=str(item["topology"]),
                input_target_ids=tuple(input_targets),
                trigger_id=str(item["triggerId"]),
                old_target_id=old_target,
                new_target_id=new_target,
                new_target_name=str(item["newTargetName"]),
            )
        )
    if not entries:
        raise ManagedSwitchBindingMigrationConflict(
            "protected binding topology is missing"
        )
    return tuple(entries)
