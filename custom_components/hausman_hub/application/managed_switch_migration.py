"""Verified, restart-safe migration for release-owned switch scenarios."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import inspect
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from .native_automation_migration import NativeAutomationNotReady

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

MIGRATION_ID = "managed-switches"
MIGRATION_VERSION = 3
MANAGED_TOPOLOGY = "managed-three-node-v1"
_KNOWN_COMPLETED_V2_RECEIPT = {
    "migrationId": MIGRATION_ID,
    "version": 2,
    "state": "completed",
    "manifestHash": "a3554f0a7108160238cbd4fd49f2f643ad3d446d7f344dec618d9a0d979d2c60",
}

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
    operation: str = "replace"
    expected_revision: int | None = 0
    activation_ready: bool = False


_SHOWER_LEGACY_INPUTS = (
    "entity_d1fb2cbf2a691bba", "entity_fd3945cf1a2110f8",
    "entity_6b9ccdab9bb484b2", "entity_46174e1ff9913212",
    "entity_1fdcd8b244637246", "entity_afef5df0e0cae309",
    "entity_e7a7c61eec7bdff8",
)
_SHOWER_INPUTS = _SHOWER_LEGACY_INPUTS
_SMALL_LEGACY_INPUTS = (
    "entity_90417aada6a33491", "entity_6b9ccdab9bb484b2",
    "entity_5f3b4436fb7b6f2b", "entity_c9d6bc67f172f30d",
    "entity_4be32416634e6416", "entity_9ed909332fdaa8fd",
)
_SMALL_INPUTS = _SMALL_LEGACY_INPUTS
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
        "system-shower-comfort-controller", 4,
        "757bde711c85ebad4826c2ec0bf2695d0034f7dd820c9ec7c30816f3f37c1551",
        MANAGED_TOPOLOGY, _SHOWER_LEGACY_INPUTS, _SHOWER_INPUTS,
        "757bde711c85ebad4826c2ec0bf2695d0034f7dd820c9ec7c30816f3f37c1551",
        "shower_controller.js", expected_revision=4,
    ),
    ManagedSwitchMigrationEntry(
        "system-small-corridor-light-controller", 3,
        "bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1",
        MANAGED_TOPOLOGY, _SMALL_LEGACY_INPUTS, _SMALL_INPUTS,
        "bc9a2c7883046e568a428e355af312953d70f0f504393b063130f516fe5052b1",
        "small_corridor_controller.js", expected_revision=3,
    ),
    ManagedSwitchMigrationEntry(
        "system-tambur-adaptive-controller", 8,
        "4daef9ac2de8dc1c95dd2da6887e178751a65d0e47bcf48443635f68eb1ba5dc",
        MANAGED_TOPOLOGY, _TAMBUR_INPUTS, _TAMBUR_INPUTS,
        "4daef9ac2de8dc1c95dd2da6887e178751a65d0e47bcf48443635f68eb1ba5dc",
        "tambur_controller.js", expected_revision=8,
    ),
    ManagedSwitchMigrationEntry(
        "system-toilet-comfort-controller", 0,
        "d46cae51f74459a617aff70b8d056f0bca961968f9d173b1d17b5769b2351579",
        MANAGED_TOPOLOGY,
        _TOILET_INPUTS, _TOILET_INPUTS,
        "d46cae51f74459a617aff70b8d056f0bca961968f9d173b1d17b5769b2351579",
        "toilet_controller.js", operation="create", expected_revision=None,
    ),
    ManagedSwitchMigrationEntry(
        "system-bathroom-exhaust-controller", 0,
        "588864e71c899dd6d57c42f3040393be5793b57ebec243bf609ec407dc92f8ad",
        MANAGED_TOPOLOGY,
        _BATHROOM_INPUTS, _BATHROOM_INPUTS,
        "588864e71c899dd6d57c42f3040393be5793b57ebec243bf609ec407dc92f8ad",
        "bathroom_controller.js", operation="create", expected_revision=None,
    ),
    ManagedSwitchMigrationEntry(
        "system-storage-light-controller", 0,
        "b46671a5f83c5fba9e7dd5fcfbd15f0132d5b5ca68c2291f1817fa3e1f03d4db",
        MANAGED_TOPOLOGY,
        _STORAGE_INPUTS, _STORAGE_INPUTS,
        "b46671a5f83c5fba9e7dd5fcfbd15f0132d5b5ca68c2291f1817fa3e1f03d4db",
        "storage_controller.js", operation="create", expected_revision=None,
    ),
    ManagedSwitchMigrationEntry(
        "system-cabinet-light-controller", 0,
        "d6f7d43bf964a1bc565b905427239d730f57650effd51dfca1b4e2eb7702bc78",
        MANAGED_TOPOLOGY,
        _CABINET_INPUTS, _CABINET_INPUTS,
        "d6f7d43bf964a1bc565b905427239d730f57650effd51dfca1b4e2eb7702bc78",
        "cabinet_controller.js", operation="create", expected_revision=None,
    ),
    ManagedSwitchMigrationEntry(
        "system-curtains-privacy-controller", 0,
        "d60c10c32f0f689a7f0fe1a31466d4825454cdec00a67590a10bcfdc44cf54cc",
        MANAGED_TOPOLOGY,
        _CURTAIN_INPUTS, _CURTAIN_INPUTS,
        "d60c10c32f0f689a7f0fe1a31466d4825454cdec00a67590a10bcfdc44cf54cc",
        "curtains_controller.js", operation="create", expected_revision=None,
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
            "operation": item.operation,
            "expectedRevision": item.expected_revision,
            "legacyRevision": item.legacy_revision,
            "legacySourceHash": item.legacy_source_hash,
            "legacyTopology": item.legacy_topology,
            "legacyInputs": item.legacy_input_target_ids,
            "inputs": item.input_target_ids,
            "newSourceHash": item.new_source_hash,
            "sourceFile": item.source_file,
            "activationReady": item.activation_ready,
        }
        for item in manifest
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


MANIFEST_HASH = _manifest_hash_for(MIGRATION_MANIFEST)
FULL_MANIFEST_HASH = _manifest_hash_for(FULL_MIGRATION_MANIFEST)


def valid_managed_switch_migration_payload(value: object) -> bool:
    required = {"migrationId", "version", "state", "manifestHash"}
    return bool(
        isinstance(value, Mapping)
        and set(value) == required | {"journal"}
        and value.get("migrationId") == MIGRATION_ID
        and value.get("version") == MIGRATION_VERSION
        and value.get("state") in {"prepared", "completed"}
        and value.get("manifestHash") == MANIFEST_HASH
        and (
            _valid_migration_journal(value.get("journal"))
            and (
                value.get("state") != "completed"
                or (
                    value["journal"].get("after") is not None
                    and value["journal"]["registry"].get("state") == "applied"
                    and all(
                        record.get("state") == "applied"
                        for record in value["journal"]["operations"].values()
                    )
                )
            )
        )
    )


def _valid_registry_image(value: object) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"version", "scenarios"}
        or not isinstance(value.get("version"), int)
        or not isinstance(value.get("scenarios"), list)
    ):
        return False
    scenario_ids = [
        item.get("id") if isinstance(item, Mapping) else None
        for item in value["scenarios"]
    ]
    return bool(
        all(isinstance(scenario_id, str) and scenario_id for scenario_id in scenario_ids)
        and len(scenario_ids) == len(set(scenario_ids))
    )


def _valid_flow_image(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("state") == "absent":
        return set(value) == {"state"}
    return bool(
        set(value) == {
            "state", "flowId", "sourceHash", "source", "topology"
        }
        and value.get("state") == "present"
        and all(
            isinstance(value.get(key), str) and bool(value.get(key))
            for key in ("flowId", "sourceHash", "source", "topology")
        )
    )


def _valid_capture_image(value: object) -> bool:
    return bool(
        isinstance(value, Mapping)
        and set(value) == {"registry", "flows"}
        and _valid_registry_image(value.get("registry"))
        and isinstance(value.get("flows"), Mapping)
        and set(value["flows"]) == {
            item.scenario_id for item in MIGRATION_MANIFEST
        }
        and all(_valid_flow_image(item) for item in value["flows"].values())
    )


_OPERATION_KEYS = {
    "kind",
    "state",
    "flowId",
    "flowRevision",
    "expectedSourceHash",
    "newSourceHash",
    "previousSource",
}


def _valid_staged_operation(
    scenario_id: str, value: object, before: Mapping[str, object]
) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != _OPERATION_KEYS
        or value.get("kind") not in {"replace", "create"}
        or value.get("state") not in {"pending", "intent", "applied"}
        or not isinstance(value.get("newSourceHash"), str)
        or not value.get("newSourceHash")
    ):
        return False
    before_flow = before["flows"].get(scenario_id)
    if value["kind"] == "replace":
        return bool(
            isinstance(before_flow, Mapping)
            and before_flow.get("state") == "present"
            and value.get("flowId") == before_flow.get("flowId")
            and value.get("expectedSourceHash") == before_flow.get("sourceHash")
            and value.get("previousSource") == before_flow.get("source")
            and (
                value.get("flowRevision") is None
                or isinstance(value.get("flowRevision"), int)
            )
        )
    return bool(
        isinstance(before_flow, Mapping)
        and before_flow.get("state") == "absent"
        and value.get("expectedSourceHash") is None
        and value.get("previousSource") is None
        and (
            value.get("flowId") is None
            or isinstance(value.get("flowId"), str)
        )
        and (
            value.get("flowRevision") is None
            or isinstance(value.get("flowRevision"), int)
        )
        and (
            value.get("state") != "applied"
            or (
                isinstance(value.get("flowId"), str)
                and bool(value.get("flowId"))
                and isinstance(value.get("flowRevision"), int)
            )
        )
    )


def _valid_migration_journal(value: object) -> bool:
    if (
        not isinstance(value, Mapping)
        or set(value) != {"before", "after", "operations", "registry"}
        or not _valid_capture_image(value.get("before"))
        or (
            value.get("after") is not None
            and not _valid_capture_image(value.get("after"))
        )
        or not isinstance(value.get("operations"), Mapping)
        or not isinstance(value.get("registry"), Mapping)
    ):
        return False
    manifest = {item.scenario_id: item for item in MIGRATION_MANIFEST}
    operations = value["operations"]
    if set(operations) != set(manifest):
        return False
    for scenario_id, operation in operations.items():
        item = manifest[scenario_id]
        if (
            not _valid_staged_operation(
                scenario_id,
                operation,
                value["before"],
            )
            or operation.get("kind") != item.operation
            or operation.get("newSourceHash") != item.new_source_hash
        ):
            return False
    registry = value["registry"]
    if (
        set(registry) != {"state", "beforeHash", "afterHash"}
        or registry.get("state") not in {"pending", "intent", "applied"}
        or registry.get("beforeHash")
        != _storage_hash(value["before"]["registry"])
    ):
        return False
    after_hash = registry.get("afterHash")
    if registry["state"] == "pending":
        if after_hash is not None:
            return False
    elif not isinstance(after_hash, str) or not after_hash:
        return False
    after = value.get("after")
    if after is None:
        return True
    if (
        registry["state"] != "applied"
        or after_hash != _storage_hash(after["registry"])
    ):
        return False
    return all(
        after["flows"][scenario_id].get("state") == "present"
        and after["flows"][scenario_id].get("sourceHash")
        == item.new_source_hash
        and after["flows"][scenario_id].get("topology")
        == item.legacy_topology
        for scenario_id, item in manifest.items()
    )


def _storage_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()


def _clone_json(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _initial_journal(
    before: Mapping[str, object],
    manifest: tuple[ManagedSwitchMigrationEntry, ...],
) -> dict[str, object]:
    operations: dict[str, object] = {}
    flows = before["flows"]
    for item in manifest:
        flow = flows[item.scenario_id]
        operations[item.scenario_id] = {
            "kind": item.operation,
            "state": "pending",
            "flowId": flow.get("flowId") if item.operation == "replace" else None,
            "flowRevision": None,
            "expectedSourceHash": (
                flow.get("sourceHash") if item.operation == "replace" else None
            ),
            "newSourceHash": item.new_source_hash,
            "previousSource": (
                flow.get("source") if item.operation == "replace" else None
            ),
        }
    return {
        "before": _clone_json(before),
        "after": None,
        "operations": operations,
        "registry": {
            "state": "pending",
            "beforeHash": _storage_hash(before["registry"]),
            "afterHash": None,
        },
    }


def _is_known_completed_v2_receipt(value: object) -> bool:
    """Allow exactly the receipt observed before the v3 migration format."""

    return isinstance(value, Mapping) and dict(value) == _KNOWN_COMPLETED_V2_RECEIPT


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
                item.operation, item.expected_revision, item.activation_ready,
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
            if not all(item.activation_ready for item in self._manifest):
                self._terminal = True
                self._unsubscribe()
                self._publish("blocked", "managed_controller_content_incomplete")
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
            except NativeAutomationNotReady:
                if final:
                    self._terminal = True
                    self._unsubscribe()
                    self._publish("blocked", "native_automation_retry_exhausted")
                else:
                    self._publish("waiting", "native_automation_warmup")
                return
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
            payload_validator=lambda value: (
                valid_managed_switch_migration_payload(value)
                or _is_known_completed_v2_receipt(value)
            ),
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
        native_automation_migration: object | None = None,
        manifest: tuple[ManagedSwitchMigrationEntry, ...] = MIGRATION_MANIFEST,
    ) -> None:
        self._service = service
        self._store = store
        self._source_loader = source_loader
        self._native_automation_migration = native_automation_migration
        self._manifest = manifest
        self._manifest_hash = _manifest_hash_for(manifest)

    def _receipt(
        self, state: str, journal: dict[str, object] | None = None
    ) -> dict[str, object]:
        receipt: dict[str, object] = {
            "migrationId": MIGRATION_ID,
            "version": MIGRATION_VERSION,
            "state": state,
            "manifestHash": self._manifest_hash,
        }
        if journal is not None:
            receipt["journal"] = journal
        return receipt

    async def async_apply(self) -> str:
        loaded = await self._store.async_load()
        legacy_completed = _is_known_completed_v2_receipt(loaded)
        if loaded is not None and not legacy_completed and not (
            valid_managed_switch_migration_payload(loaded)
            and loaded.get("manifestHash") == self._manifest_hash
        ):
            raise ManagedSwitchMigrationConflict("migration receipt is invalid")
        completed = (
            not legacy_completed
            and isinstance(loaded, Mapping)
            and loaded.get("state") == "completed"
            and _valid_migration_journal(loaded.get("journal"))
        )
        apply = getattr(self._service, "async_apply_managed_switch_migration", None)
        if not callable(apply):
            raise ManagedSwitchMigrationConflict("scenario migration CAS is unavailable")
        entries = (
            await self._source_loader()
            if self._source_loader is not None
            else _entries_with_sources(self._manifest)
        )
        if self._native_automation_migration is not None:
            require_ready = getattr(
                self._native_automation_migration, "async_require_ready", None
            )
            if callable(require_ready):
                await require_ready()
        capture = getattr(
            self._service, "async_capture_managed_switch_migration", None
        )
        if not callable(capture):
            raise ManagedSwitchMigrationConflict("scenario migration journal is unavailable")
        if completed:
            journal = _clone_json(loaded["journal"])
            verify = getattr(
                self._service, "async_verify_managed_switch_migration", None
            )
            if not callable(verify):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration final CAS verification is unavailable"
                )
            await verify(entries)
            current = await capture(entries)
            if _clone_json(current) != journal["after"]:
                raise ManagedSwitchMigrationConflict(
                    "scenario migration completed image drifted"
                )
            if self._native_automation_migration is not None:
                native_verify = getattr(
                    self._native_automation_migration,
                    "async_verify_completed",
                    None,
                )
                if not callable(native_verify) or not await native_verify():
                    raise ManagedSwitchMigrationConflict(
                        "native automation completion drifted"
                    )
            return "completed"

        if (
            isinstance(loaded, Mapping)
            and loaded.get("state") == "prepared"
            and _valid_migration_journal(loaded.get("journal"))
        ):
            journal = _clone_json(loaded["journal"])
        else:
            before = await capture(entries)
            if not _valid_capture_image(before):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration before-image is invalid"
                )
            journal = _initial_journal(before, entries)
            await self._store.async_save(self._receipt("prepared", journal))

        stage = getattr(self._service, "async_stage_managed_switch_migration", None)
        applied = False
        native_applied = False
        try:
            if callable(stage):
                async def persist_staged(staged: Mapping[str, object]) -> None:
                    journal["operations"] = _clone_json(staged)
                    await self._store.async_save(
                        self._receipt("prepared", journal)
                    )

                stage_parameters = inspect.signature(stage).parameters
                if "journal" in stage_parameters:
                    await stage(
                        entries,
                        journal=journal,
                        on_staged=persist_staged,
                    )
                else:
                    await stage(entries, on_staged=persist_staged)
            apply_parameters = inspect.signature(apply).parameters

            async def persist_registry(state: Mapping[str, object]) -> None:
                journal["registry"] = _clone_json(state)
                await self._store.async_save(self._receipt("prepared", journal))

            if "journal" in apply_parameters:
                await apply(
                    entries,
                    journal=journal,
                    on_registry=persist_registry,
                )
            else:
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
            if self._native_automation_migration is not None:
                native_apply = getattr(
                    self._native_automation_migration, "async_apply", None
                )
                if not callable(native_apply):
                    raise ManagedSwitchMigrationConflict(
                        "native automation handover is unavailable"
                    )
                await native_apply()
                native_applied = True
            finalize = getattr(
                self._service,
                "async_finalize_managed_switch_migration",
                None,
            )
            if not callable(finalize):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration finalization is unavailable"
                )
            finalize_parameters = inspect.signature(finalize).parameters
            if "journal" in finalize_parameters:
                await finalize(
                    entries,
                    journal=journal,
                    on_registry=persist_registry,
                )
            else:
                await finalize(entries)
            after = await capture(entries)
            if not _valid_capture_image(after):
                raise ManagedSwitchMigrationConflict(
                    "scenario migration after-image is invalid"
                )
            journal["after"] = _clone_json(after)
            await self._store.async_save(self._receipt("completed", journal))
            await verify(entries)
            if _clone_json(await capture(entries)) != journal["after"]:
                raise ManagedSwitchMigrationConflict(
                    "scenario migration final image changed"
                )
            if self._native_automation_migration is not None:
                native_verify = getattr(
                    self._native_automation_migration,
                    "async_verify_completed",
                    None,
                )
                if not callable(native_verify) or not await native_verify():
                    raise ManagedSwitchMigrationConflict(
                        "native automation final image changed"
                    )
            commit = getattr(
                self._service, "async_commit_managed_switch_migration", None
            )
            if callable(commit):
                await commit(entries)
        except BaseException as error:
            try:
                await asyncio.shield(
                    self._store.async_save(self._receipt("prepared", journal))
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
                    rollback_parameters = inspect.signature(rollback).parameters
                    rollback_call = (
                        rollback(entries, journal=journal)
                        if "journal" in rollback_parameters
                        else rollback(entries)
                    )
                    rollback_complete = (
                        await asyncio.shield(rollback_call) is True
                    )
                except BaseException:  # noqa: BLE001
                    rollback_complete = False
            if not rollback_complete:
                raise ManagedSwitchMigrationConflict(
                    "managed switch migration recovery is required"
                ) from error
            if native_applied:
                native_rollback = getattr(
                    self._native_automation_migration, "async_rollback", None
                )
                if not callable(native_rollback) or not await native_rollback():
                    raise ManagedSwitchMigrationConflict(
                        "native automation recovery is required"
                    ) from error
            reset = _initial_journal(journal["before"], entries)
            try:
                await asyncio.shield(
                    self._store.async_save(self._receipt("prepared", reset))
                )
            except BaseException as recovery_error:
                raise ManagedSwitchMigrationConflict(
                    "managed switch migration journal recovery is required"
                ) from recovery_error
            raise
        return "completed"
