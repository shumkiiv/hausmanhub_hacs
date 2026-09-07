"""Verified, restart-safe migration for release-owned switch scenarios."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

MIGRATION_ID = "managed-switches"
MIGRATION_VERSION = 2
MANAGED_TOPOLOGY = "managed-three-node-v1"

_LOGGER = logging.getLogger(__name__)


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

# Only stable catalog IDs may cross the scenario boundary.  Unknown live
# bindings remain absent and make that controller safely skip until bound.
_TOILET_INPUTS = (
    "entity_ce73f88bda2e6812", "entity_56650c782076ed4d",
    "entity_5d95de599d2b5cec", "entity_6667b3400bce7970",
    "entity_9bbb3b0e8cd98627", "entity_6b9ccdab9bb484b2",
    "entity_3f343b8d6f58f5b4",
)
_BATHROOM_INPUTS = (
    "entity_a591e035e3e5b34f", "entity_d82766182d69dd51",
    "entity_436e12f71ce7b08b", "entity_c15f5df5382ee180",
)
_STORAGE_INPUTS = ("entity_00dcf0ebdc0bc6cb", "entity_0ec37ef18b4b39a6")
_CABINET_INPUTS = (
    "entity_aeaf7c250c68e8c2", "entity_7ff6d09cfa68fa5a",
    "entity_5f3b4436fb7b6f2b", "entity_6b9ccdab9bb484b2",
)
_CURTAIN_INPUTS = (
    "entity_8746cfd7f6f7103d", "entity_2da2065add6e2168",
    "entity_1e0b476b7d082cc0", "entity_9164132c7692d6f5",
)

FULL_MIGRATION_MANIFEST: tuple[ManagedSwitchMigrationEntry, ...] = (
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
    ManagedSwitchMigrationEntry(
        "system-toilet-comfort-controller", 1, "0" * 64, MANAGED_TOPOLOGY,
        _TOILET_INPUTS, _TOILET_INPUTS,
        "d46cae51f74459a617aff70b8d056f0bca961968f9d173b1d17b5769b2351579",
        "toilet_controller.js",
    ),
    ManagedSwitchMigrationEntry(
        "system-bathroom-exhaust-controller", 1, "1" * 64, MANAGED_TOPOLOGY,
        _BATHROOM_INPUTS, _BATHROOM_INPUTS,
        "588864e71c899dd6d57c42f3040393be5793b57ebec243bf609ec407dc92f8ad",
        "bathroom_controller.js",
    ),
    ManagedSwitchMigrationEntry(
        "system-storage-light-controller", 1, "2" * 64, MANAGED_TOPOLOGY,
        _STORAGE_INPUTS, _STORAGE_INPUTS,
        "b46671a5f83c5fba9e7dd5fcfbd15f0132d5b5ca68c2291f1817fa3e1f03d4db",
        "storage_controller.js",
    ),
    ManagedSwitchMigrationEntry(
        "system-cabinet-light-controller", 1, "3" * 64, MANAGED_TOPOLOGY,
        _CABINET_INPUTS, _CABINET_INPUTS,
        "d6f7d43bf964a1bc565b905427239d730f57650effd51dfca1b4e2eb7702bc78",
        "cabinet_controller.js",
    ),
    ManagedSwitchMigrationEntry(
        "system-curtains-privacy-controller", 1, "4" * 64, MANAGED_TOPOLOGY,
        _CURTAIN_INPUTS, _CURTAIN_INPUTS,
        "d60c10c32f0f689a7f0fe1a31466d4825454cdec00a67590a10bcfdc44cf54cc",
        "curtains_controller.js",
    ),
)
# The active release manifest is the single authoritative set.  The old
# three-controller set is compatibility history only and must never drive
# startup, receipts, or execution.
MIGRATION_MANIFEST = FULL_MIGRATION_MANIFEST
LEGACY_MANAGED_SWITCHES = {
    item.scenario_id: (item.legacy_revision, item.legacy_source_hash)
    for item in FULL_MIGRATION_MANIFEST
}


class ManagedSwitchMigrationConflict(RuntimeError):
    """The durable receipt or live CAS evidence does not match the manifest."""


@dataclass(frozen=True, slots=True)
class ManagedSwitchActivation:
    """Prepared runtime callbacks and their coordinator-owned activation gate."""

    cleanup: Callable[[], None]
    commit: Callable[[], None]
    revoke: Callable[[], None]


def _manifest_hash_for(manifest: tuple[ManagedSwitchMigrationEntry, ...]) -> str:
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
        for item in manifest
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


MANIFEST_HASH = _manifest_hash_for(MIGRATION_MANIFEST)
FULL_MANIFEST_HASH = _manifest_hash_for(FULL_MIGRATION_MANIFEST)


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


def _entries_with_sources(
    manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
) -> tuple[ManagedSwitchMigrationEntry, ...]:
    root = Path(__file__).resolve().parents[1] / "managed_scenarios"
    entries: list[ManagedSwitchMigrationEntry] = []
    for item in manifest:
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


async def async_load_managed_switch_migration_entries(
    add_executor_job: Callable[..., Awaitable[object]],
    manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
) -> tuple[ManagedSwitchMigrationEntry, ...]:
    entries = await add_executor_job(_entries_with_sources, manifest)
    if not isinstance(entries, tuple) or not all(
        isinstance(item, ManagedSwitchMigrationEntry) for item in entries
    ):
        raise ManagedSwitchMigrationConflict(
            "release-owned source loading returned invalid data"
        )
    return entries


class ManagedSwitchStartupCoordinator:
    def __init__(
        self,
        service: object,
        migration: object,
        activate: Callable[[], Awaitable[object]],
        *,
        binding_migration: object | None = None,
        status_publisher: Callable[[dict[str, str]], None] | None = None,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> None:
        self._service = service
        self._migration = migration
        self._binding_migration = binding_migration
        self._activate = activate
        self._status_publisher = status_publisher or (lambda _status: None)
        self._manifest = manifest
        self._remove_observer: Callable[[], None] | None = None
        self._activation_task: asyncio.Task[object] | None = None
        self._activation_cleanup: Callable[[], None] | None = None
        self._activation_commit: Callable[[], None] | None = None
        self._activation_revoke: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._cancelled = False
        self._terminal = False
        self.activation_authorized = False
        self.ready = False

    async def async_start(self) -> None:
        if self._started or self._cancelled:
            return
        self._started = True
        add_observer = getattr(
            self._service, "add_catalog_warmup_observer", None
        )
        if not callable(add_observer):
            self._terminal = True
            self._publish("blocked", "catalog_warmup_unavailable")
            return
        self._remove_observer = add_observer(self._async_catalog_snapshot)
        await self._async_attempt(self._service.current_catalog(), final=False)

    def cancel(self) -> None:
        self._cancelled = True
        self.activation_authorized = False
        self.ready = False
        self._unsubscribe()
        activation_task = self._activation_task
        self._adopt_completed_activation(activation_task)
        if (
            activation_task is not None
            and not activation_task.done()
            and activation_task is not asyncio.current_task()
        ):
            activation_task.cancel()
        self._revoke_activation()
        self._cleanup_activation()

    async def _async_catalog_snapshot(self, catalog: object, final: bool) -> None:
        await self._async_attempt(catalog, final=final)

    async def _async_attempt(self, catalog: object, *, final: bool) -> None:
        async with self._lock:
            if self._cancelled or self._terminal or self.ready:
                return
            if not self._catalog_has_required_targets(catalog, self._manifest):
                if final:
                    self._terminal = True
                    self._unsubscribe()
                    self._publish("blocked", "catalog_retry_exhausted")
                else:
                    self._publish("waiting", "catalog_warmup")
                return
            try:
                await self._migration.async_apply()
                if self._binding_migration is not None:
                    await self._binding_migration.async_apply()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "verified_migration_failed")
                _LOGGER.error("Release-owned managed switch migration is blocked")
                return
            if self._cancelled:
                return
            self.activation_authorized = True
            activation_task = asyncio.create_task(self._activate())
            self._activation_task = activation_task
            try:
                activation = self._activation_from_result(await activation_task)
            except asyncio.CancelledError:
                self.activation_authorized = False
                self._revoke_activation()
                self._cleanup_activation()
                raise
            except Exception:  # noqa: BLE001
                self.activation_authorized = False
                self._revoke_activation()
                self._cleanup_activation()
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "runtime_activation_failed")
                _LOGGER.error("Managed switch runtime activation is blocked")
                return
            finally:
                if self._activation_task is activation_task:
                    self._activation_task = None
            if self._cancelled or not self.activation_authorized:
                self._revoke_activation()
                self._cleanup_activation()
                return
            self._set_activation(activation)
            try:
                self._commit_activation()
            except Exception:  # noqa: BLE001
                self.activation_authorized = False
                self._revoke_activation()
                self._cleanup_activation()
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "runtime_activation_failed")
                _LOGGER.error("Managed switch runtime activation is blocked")
                return
            self.ready = True
            self._terminal = True
            self._unsubscribe()
            self._publish("completed")

    @staticmethod
    def _catalog_has_required_targets(
        catalog: object,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> bool:
        resolve = getattr(catalog, "device", None)
        if not callable(resolve):
            return False
        return all(
            getattr(resolve(target_id), "target_id", None) == target_id
            for target_id in dict.fromkeys(
                target_id
                for entry in manifest
                for target_id in entry.input_target_ids
            )
        )

    def _publish(self, state: str, reason: str | None = None) -> None:
        payload = {"state": state}
        if reason is not None:
            payload["reason"] = reason
        self._status_publisher(payload)

    def _unsubscribe(self) -> None:
        remove = self._remove_observer
        self._remove_observer = None
        if remove is not None:
            remove()

    def _cleanup_activation(self) -> None:
        cleanup = self._activation_cleanup
        self._activation_cleanup = None
        if cleanup is None:
            return
        try:
            cleanup()
        except Exception:  # noqa: BLE001
            _LOGGER.error("Managed switch activation cleanup failed")

    @staticmethod
    def _activation_from_result(result: object) -> ManagedSwitchActivation:
        if isinstance(result, ManagedSwitchActivation):
            return result
        if callable(result):
            return ManagedSwitchActivation(result, lambda: None, lambda: None)
        cleanup = getattr(result, "cleanup", None)
        commit = getattr(result, "commit", None)
        revoke = getattr(result, "revoke", None)
        if all(callable(item) for item in (cleanup, commit, revoke)):
            return ManagedSwitchActivation(cleanup, commit, revoke)
        if result is None:
            return ManagedSwitchActivation(lambda: None, lambda: None, lambda: None)
        raise TypeError("managed switch activation returned an invalid handle")

    def _adopt_completed_activation(self, task: asyncio.Task[object] | None) -> None:
        if task is None or not task.done() or self._activation_cleanup is not None:
            return
        try:
            activation = self._activation_from_result(task.result())
        except (asyncio.CancelledError, Exception):
            return
        self._set_activation(activation)

    def _set_activation(self, activation: ManagedSwitchActivation) -> None:
        self._activation_cleanup = activation.cleanup
        self._activation_commit = activation.commit
        self._activation_revoke = activation.revoke

    def _commit_activation(self) -> None:
        commit = self._activation_commit
        self._activation_commit = None
        if commit is None:
            raise RuntimeError("managed switch activation was not prepared")
        commit()

    def _revoke_activation(self) -> None:
        revoke = self._activation_revoke
        self._activation_revoke = None
        if revoke is None:
            return
        try:
            revoke()
        except Exception:  # noqa: BLE001
            _LOGGER.error("Managed switch activation revoke failed")


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
    def __init__(
        self,
        service: object,
        store: object,
        *,
        source_loader: Callable[
            [], Awaitable[tuple[ManagedSwitchMigrationEntry, ...]]
        ]
        | None = None,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> None:
        self._service = service
        self._store = store
        self._source_loader = source_loader
        self._manifest = manifest
        self._manifest_hash = _manifest_hash_for(manifest)

    def _receipt(self, state: str) -> dict[str, object]:
        return {
            "migrationId": MIGRATION_ID,
            "version": MIGRATION_VERSION,
            "state": state,
            "manifestHash": self._manifest_hash,
        }

    async def async_apply(self) -> str:
        loaded = await self._store.async_load()
        if loaded is not None and not (
            valid_managed_switch_migration_payload(loaded)
            and loaded.get("manifestHash") == self._manifest_hash
        ):
            raise ManagedSwitchMigrationConflict("migration receipt is invalid")
        completed = isinstance(loaded, Mapping) and loaded.get("state") == "completed"
        if loaded is None:
            await self._store.async_save(self._receipt("prepared"))
        apply = getattr(self._service, "async_apply_managed_switch_migration", None)
        if not callable(apply):
            raise ManagedSwitchMigrationConflict("scenario migration CAS is unavailable")
        entries = (
            await self._source_loader()
            if self._source_loader is not None
            else _entries_with_sources(self._manifest)
        )
        applied = False
        try:
            await apply(entries)
            applied = True
            verify = getattr(
                self._service,
                "async_verify_managed_switch_migration",
                None,
            )
            if not callable(verify):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration final CAS verification is unavailable"
                )
            await verify(entries)
            if not completed:
                await self._store.async_save(self._receipt("completed"))
                # A receipt write is not execution authority by itself. Recheck
                # after persistence so drift in that gap keeps the adapter off.
                await verify(entries)
            finalize = getattr(
                self._service,
                "async_finalize_managed_switch_migration",
                None,
            )
            if not callable(finalize):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration finalization is unavailable"
                )
            await finalize(entries)
        except (Exception, asyncio.CancelledError) as error:
            if not applied:
                raise
            try:
                await asyncio.shield(
                    self._store.async_save(self._receipt("prepared"))
                )
            except Exception:  # noqa: BLE001
                pass
            rollback = getattr(
                self._service,
                "async_rollback_managed_switch_migration",
                None,
            )
            rollback_complete = False
            if callable(rollback):
                try:
                    rollback_complete = await asyncio.shield(
                        rollback(entries)
                    ) is True
                except Exception:  # noqa: BLE001
                    rollback_complete = False
            if not rollback_complete:
                raise ManagedSwitchMigrationConflict(
                    "managed switch migration recovery is required"
                ) from error
            raise
        return "completed"
