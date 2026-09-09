"""Restart-safe migration and activation boundary for the Tambur only."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING

from .managed_switch_migration import (
    _is_known_completed_v2_receipt,
    valid_managed_switch_migration_payload,
)
from .native_automation_migration import (
    EXPECTED_NATIVE_AUTOMATIONS,
    HomeAssistantNativeAutomationAdapter,
    NativeAutomationMigrationConflict,
    NativeAutomationNotReady,
)
from .scenario_node_red_decision import (
    TAMBUR_DECISION_TOPOLOGY,
    prepare_tambur_decision_bundle,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


TAMBUR_SCENARIO_ID = "system-tambur-adaptive-controller"
TAMBUR_PRESENCE_TARGET_IDS = (
    "entity_156050daca86aa6c",
    "entity_402b26d150a1ef3f",
    "entity_10b78187426f8485",
)
TAMBUR_INPUT_TARGET_IDS = (
    *TAMBUR_PRESENCE_TARGET_IDS,
    "entity_6b9ccdab9bb484b2",
    "entity_71859313239a14e4",
    "entity_cd0098e5ff95da46",
    "entity_fbdf27871edb89bf",
    "entity_b47991988cc6b9f3",
    "entity_170c7a4e2505b803",
)
TAMBUR_NATIVE_COMPETITORS = (
    "automation.tambur_nochiu_vykliuchit_svet_cherez_3_minuty_otsutstviia",
    "automation.tambur_svet_po_liuboi_klavishe_vykliuchatelia_zerkala",
)
TAMBUR_ROOM_MIGRATION_STAGES = frozenset(
    {
        "receipt_load",
        "source_validation",
        "native_readiness",
        "capture",
        "stage",
        "registry_apply",
        "verify",
        "native_handover",
        "finalize",
        "binding",
    }
)

_MIGRATION_ID = "tambur-room-decision"
_MIGRATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class TamburRoomMigrationPlan:
    scenario_id: str
    legacy_revision: int
    legacy_flow_id: str
    legacy_source_hash: str
    legacy_topology: str
    legacy_input_target_ids: tuple[str, ...]
    input_target_ids: tuple[str, ...]
    decision_flow_id: str
    decision_topology: str
    decision_topology_hash: str


def _build_plan() -> TamburRoomMigrationPlan:
    bundle = prepare_tambur_decision_bundle()
    return TamburRoomMigrationPlan(
        scenario_id=TAMBUR_SCENARIO_ID,
        legacy_revision=8,
        legacy_flow_id="a7a6f6b13cc83ca0",
        legacy_source_hash=(
            "4daef9ac2de8dc1c95dd2da6887e178751a65d0e47bcf48443635f68eb1ba5dc"
        ),
        legacy_topology="managed-three-node-v1",
        legacy_input_target_ids=(
            "entity_156050daca86aa6c",
            "entity_10b78187426f8485",
            "entity_6b9ccdab9bb484b2",
            "entity_5f3b4436fb7b6f2b",
            "entity_71859313239a14e4",
            "entity_cd0098e5ff95da46",
            "entity_fbdf27871edb89bf",
            "entity_b47991988cc6b9f3",
            "entity_170c7a4e2505b803",
        ),
        input_target_ids=TAMBUR_INPUT_TARGET_IDS,
        decision_flow_id=str(bundle["id"]),
        decision_topology=TAMBUR_DECISION_TOPOLOGY,
        decision_topology_hash=str(bundle["topologyHash"]),
    )


TAMBUR_ROOM_PLAN = _build_plan()


def _clone(value: object) -> object:
    return copy.deepcopy(value)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


_PLAN_HASH = _digest(
    {
        field: getattr(TAMBUR_ROOM_PLAN, field)
        for field in TAMBUR_ROOM_PLAN.__dataclass_fields__
    }
)


class TamburRoomMigrationConflict(RuntimeError):
    """A fail-closed room migration error with a public safe stage code."""

    def __init__(self, stage: str) -> None:
        if stage not in TAMBUR_ROOM_MIGRATION_STAGES:
            raise ValueError("Tambur room migration stage is invalid")
        super().__init__(stage)
        self.stage = stage


@dataclass(frozen=True, slots=True)
class TamburRuntimeScope:
    scenario_ids: tuple[str, ...] = (TAMBUR_SCENARIO_ID,)
    presence_target_ids: tuple[str, ...] = TAMBUR_PRESENCE_TARGET_IDS
    input_target_ids: tuple[str, ...] = TAMBUR_INPUT_TARGET_IDS


def _valid_room_receipt(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "migrationId",
        "version",
        "state",
        "planHash",
        "stage",
        "journal",
    }:
        return False
    journal = value.get("journal")
    if (
        value.get("migrationId") != _MIGRATION_ID
        or value.get("version") != _MIGRATION_VERSION
        or value.get("state") not in {"prepared", "completed"}
        or value.get("planHash") != _PLAN_HASH
        or value.get("stage") not in TAMBUR_ROOM_MIGRATION_STAGES
        or not isinstance(journal, Mapping)
        or set(journal) != {"before", "staged", "registry", "after"}
        or journal.get("before") is None
        or not isinstance(journal.get("registry"), Mapping)
    ):
        return False
    if value["state"] == "completed":
        return bool(
            value["stage"] == "binding"
            and journal.get("after") is not None
            and journal["registry"].get("state") == "finalized"
        )
    return journal.get("after") is None


def _global_receipt_allows_room(value: object) -> bool:
    return bool(
        value is None
        or _is_known_completed_v2_receipt(value)
        or (
            valid_managed_switch_migration_payload(value)
            and isinstance(value, Mapping)
            and value.get("state") == "completed"
        )
    )


class HomeAssistantTamburRoomMigrationStore:
    """Separate atomic receipt which never rewrites global migration history."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415
        from ..verified_safety_storage import VerifiedSafetyStore  # noqa: PLC0415

        backend: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.tambur_room_migration.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=_valid_room_receipt,
        )

    @property
    def recovered_previous(self) -> bool:
        return self._store.recovered_previous

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


def _valid_native_evidence(entity_id: str, value: object) -> bool:
    expected = EXPECTED_NATIVE_AUTOMATIONS.get(entity_id)
    return bool(
        expected is not None
        and isinstance(value, Mapping)
        and set(value)
        == {"state", "automationId", "definitionHash", "contextId", "lastUpdated"}
        and value.get("state") in {"on", "off"}
        and value.get("automationId") == expected["automationId"]
        and value.get("definitionHash") == expected["definitionHash"]
        and isinstance(value.get("contextId"), str)
        and bool(value.get("contextId"))
        and isinstance(value.get("lastUpdated"), str)
        and bool(value.get("lastUpdated"))
    )


def _valid_native_room_receipt(value: object) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "version", "state", "before", "operations", "after"
    }:
        return False
    before = value.get("before")
    operations = value.get("operations")
    after = value.get("after")
    if (
        value.get("version") != 1
        or value.get("state") not in {"prepared", "completed"}
        or not isinstance(before, Mapping)
        or set(before) != set(TAMBUR_NATIVE_COMPETITORS)
        or not all(_valid_native_evidence(key, item) for key, item in before.items())
        or not isinstance(operations, Mapping)
        or not set(operations) <= set(TAMBUR_NATIVE_COMPETITORS)
    ):
        return False
    for entity_id, operation in operations.items():
        if (
            not isinstance(operation, Mapping)
            or set(operation) != {"operationId", "expected", "after"}
            or not isinstance(operation.get("operationId"), str)
            or not operation.get("operationId")
            or not _valid_native_evidence(entity_id, operation.get("expected"))
            or operation.get("after") is not None
            and not _valid_native_evidence(entity_id, operation.get("after"))
        ):
            return False
    if value["state"] == "prepared":
        return after is None
    return bool(
        isinstance(after, Mapping)
        and set(after) == set(TAMBUR_NATIVE_COMPETITORS)
        and set(operations) == set(TAMBUR_NATIVE_COMPETITORS)
        and all(
            _valid_native_evidence(entity_id, after[entity_id])
            and after[entity_id].get("state") == "off"
            and operations[entity_id].get("after") == after[entity_id]
            for entity_id in TAMBUR_NATIVE_COMPETITORS
        )
    )


class HomeAssistantTamburNativeMigrationStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        from homeassistant.helpers.storage import Store  # noqa: PLC0415
        from ..verified_safety_storage import VerifiedSafetyStore  # noqa: PLC0415

        backend: Store[dict[str, object]] = Store(
            hass,
            1,
            f"hausman_hub.tambur_native_migration.{entry_id}",
            atomic_writes=True,
        )
        self._store = VerifiedSafetyStore(
            backend,
            hass.async_add_executor_job,
            payload_validator=_valid_native_room_receipt,
        )

    async def async_load(self) -> object | None:
        return await self._store.async_load()

    async def async_save(self, payload: dict[str, object]) -> None:
        await self._store.async_save(payload)


class TamburNativeAutomationMigration:
    """CAS-disable only the two proven native Tambur competitors."""

    def __init__(self, adapter: object, store: object) -> None:
        self._adapter = adapter
        self._store = store

    async def _snapshot(self) -> dict[str, object]:
        value = await self._adapter.async_snapshot(TAMBUR_NATIVE_COMPETITORS)
        if not isinstance(value, Mapping) or not all(
            _valid_native_evidence(key, item) for key, item in value.items()
        ) or set(value) != set(TAMBUR_NATIVE_COMPETITORS):
            raise NativeAutomationMigrationConflict("Tambur native evidence is incomplete")
        return copy.deepcopy(dict(value))

    async def _save(self, value: dict[str, object]) -> None:
        if not _valid_native_room_receipt(value):
            raise NativeAutomationMigrationConflict("Tambur native receipt is invalid")
        await self._store.async_save(copy.deepcopy(value))

    async def async_require_ready(self) -> None:
        await self._snapshot()

    async def async_apply(self) -> str:
        loaded = await self._store.async_load()
        if loaded is None:
            before = await self._snapshot()
            if any(before[item]["state"] != "on" for item in TAMBUR_NATIVE_COMPETITORS):
                raise NativeAutomationMigrationConflict("Tambur native baseline changed")
            journal: dict[str, object] = {
                "version": 1,
                "state": "prepared",
                "before": before,
                "operations": {},
                "after": None,
            }
            await self._save(journal)
        elif _valid_native_room_receipt(loaded):
            journal = copy.deepcopy(dict(loaded))
        else:
            raise NativeAutomationMigrationConflict("Tambur native receipt is invalid")
        if journal["state"] == "completed":
            if await self._snapshot() != journal["after"]:
                raise NativeAutomationMigrationConflict("Tambur native completion drifted")
            return "completed"
        operations = journal["operations"]
        for entity_id in TAMBUR_NATIVE_COMPETITORS:
            operation = operations.get(entity_id)
            if operation is None:
                operation_id = self._adapter.new_operation_id()
                if not isinstance(operation_id, str) or not operation_id:
                    raise NativeAutomationMigrationConflict("Tambur native context is invalid")
                operation = {
                    "operationId": operation_id,
                    "expected": copy.deepcopy(journal["before"][entity_id]),
                    "after": None,
                }
                operations[entity_id] = operation
                await self._save(journal)
            current = (await self._adapter.async_snapshot((entity_id,)))[entity_id]
            if operation["after"] is None:
                if current == operation["expected"]:
                    after = await self._adapter.async_disable(
                        entity_id,
                        operation["expected"],
                        operation_id=operation["operationId"],
                    )
                elif (
                    _valid_native_evidence(entity_id, current)
                    and current.get("state") == "off"
                    and current.get("contextId") == operation["operationId"]
                ):
                    after = current
                else:
                    raise NativeAutomationMigrationConflict(
                        "Tambur native disable outcome is ambiguous"
                    )
                operation["after"] = copy.deepcopy(after)
                await self._save(journal)
            elif current != operation["after"]:
                raise NativeAutomationMigrationConflict("Tambur native state drifted")
        after = await self._snapshot()
        if any(after[item]["state"] != "off" for item in TAMBUR_NATIVE_COMPETITORS):
            raise NativeAutomationMigrationConflict("Tambur native competitor is active")
        journal.update(state="completed", after=after)
        await self._save(journal)
        return "completed"

    async def async_verify_completed(self) -> bool:
        loaded = await self._store.async_load()
        return bool(
            _valid_native_room_receipt(loaded)
            and loaded.get("state") == "completed"
            and await self._snapshot() == loaded.get("after")
        )

    async def async_rollback(self) -> bool:
        loaded = await self._store.async_load()
        if loaded is None:
            return True
        if not _valid_native_room_receipt(loaded):
            return False
        journal = copy.deepcopy(dict(loaded))
        operations = journal["operations"]
        try:
            for entity_id in reversed(TAMBUR_NATIVE_COMPETITORS):
                operation = operations.get(entity_id)
                if operation is None:
                    continue
                current = (await self._adapter.async_snapshot((entity_id,)))[entity_id]
                after = operation["after"]
                if after is None:
                    if current == operation["expected"]:
                        continue
                    if not (
                        _valid_native_evidence(entity_id, current)
                        and current.get("state") == "off"
                        and current.get("automationId")
                        == operation["expected"].get("automationId")
                        and current.get("definitionHash")
                        == operation["expected"].get("definitionHash")
                        and current.get("contextId") == operation["operationId"]
                    ):
                        return False
                    operation["after"] = copy.deepcopy(current)
                    await self._save(journal)
                    after = current
                elif current != after:
                    return False
                await self._adapter.async_restore(
                    entity_id,
                    after,
                    journal["before"][entity_id]["state"],
                    operation_id=self._adapter.new_operation_id(),
                )
            restored = await self._snapshot()
            if any(
                restored[entity_id].get("state")
                != journal["before"][entity_id].get("state")
                or restored[entity_id].get("automationId")
                != journal["before"][entity_id].get("automationId")
                or restored[entity_id].get("definitionHash")
                != journal["before"][entity_id].get("definitionHash")
                for entity_id in TAMBUR_NATIVE_COMPETITORS
            ):
                return False
            await self._save(
                {
                    "version": 1,
                    "state": "prepared",
                    "before": restored,
                    "operations": {},
                    "after": None,
                }
            )
        except (
            NativeAutomationMigrationConflict,
            NativeAutomationNotReady,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            return False
        return True


class TamburRoomMigration:
    """Orchestrate one room without reusing the global manifest transaction."""

    def __init__(
        self,
        service: object,
        store: object,
        *,
        global_receipt_store: object,
        native_automation_migration: object,
        migration_lock: asyncio.Lock,
        plan: TamburRoomMigrationPlan = TAMBUR_ROOM_PLAN,
    ) -> None:
        self._service = service
        self._store = store
        self._global_store = global_receipt_store
        self._native = native_automation_migration
        self._lock = migration_lock
        self._plan = plan
        self.stage = "receipt_load"

    def _receipt(self, state: str, journal: Mapping[str, object]) -> dict[str, object]:
        return {
            "migrationId": _MIGRATION_ID,
            "version": _MIGRATION_VERSION,
            "state": state,
            "planHash": _PLAN_HASH,
            "stage": self.stage,
            "journal": copy.deepcopy(dict(journal)),
        }

    async def _save(self, state: str, journal: Mapping[str, object]) -> None:
        await self._store.async_save(self._receipt(state, journal))

    async def _call(self, name: str, *args: object, **kwargs: object) -> object:
        callback = getattr(self._service, name, None)
        if not callable(callback):
            raise RuntimeError(f"required room migration interface {name} is absent")
        return await callback(*args, **kwargs)

    async def async_apply(self) -> str:
        async with self._lock:
            return await self._async_apply_locked()

    async def _async_apply_locked(self) -> str:
        journal: dict[str, object] | None = None
        native_started = False
        try:
            self.stage = "receipt_load"
            global_receipt = await self._global_store.async_load()
            if not _global_receipt_allows_room(global_receipt):
                raise RuntimeError("global migration is incomplete")
            loaded = await self._store.async_load()
            if getattr(self._store, "recovered_previous", False):
                raise RuntimeError("room migration receipt recovery is ambiguous")
            if loaded is not None and not _valid_room_receipt(loaded):
                raise RuntimeError("room migration receipt is invalid")

            self.stage = "source_validation"
            if self._plan != _build_plan():
                raise RuntimeError("room migration source plan changed")

            self.stage = "native_readiness"
            await self._native.async_require_ready()

            if isinstance(loaded, Mapping) and loaded.get("state") == "completed":
                journal = copy.deepcopy(dict(loaded["journal"]))
                self.stage = "verify"
                await self._call(
                    "async_verify_tambur_room_migration",
                    self._plan,
                    journal=journal,
                    require_final=True,
                )
                current = await self._call(
                    "async_capture_tambur_room_migration", self._plan
                )
                if current != journal["after"] or not await self._native.async_verify_completed():
                    raise RuntimeError("room migration completion drifted")
                await self._call(
                    "async_commit_tambur_room_migration", self._plan, journal=journal
                )
                return "completed"

            if isinstance(loaded, Mapping):
                journal = copy.deepcopy(dict(loaded["journal"]))
            else:
                self.stage = "capture"
                before = await self._call(
                    "async_capture_tambur_room_migration", self._plan
                )
                journal = {
                    "before": copy.deepcopy(before),
                    "staged": None,
                    "registry": {"state": "pending"},
                    "after": None,
                }
                await self._save("prepared", journal)

            self.stage = "stage"
            if journal["staged"] is None:
                journal["staged"] = {"state": "intent"}
                await self._save("prepared", journal)

            async def persist_staged(value: Mapping[str, object]) -> None:
                journal["staged"] = copy.deepcopy(dict(value))
                await self._save("prepared", journal)

            staged = journal.get("staged")
            if not isinstance(staged, Mapping) or staged.get("state") != "applied":
                await self._call(
                    "async_stage_tambur_room_migration",
                    self._plan,
                    journal=journal,
                    on_staged=persist_staged,
                )

            self.stage = "registry_apply"

            async def persist_registry(value: Mapping[str, object]) -> None:
                journal["registry"] = copy.deepcopy(dict(value))
                await self._save("prepared", journal)

            await self._call(
                "async_apply_tambur_room_migration",
                self._plan,
                journal=journal,
                on_registry=persist_registry,
            )
            self.stage = "verify"
            await self._call(
                "async_verify_tambur_room_migration",
                self._plan,
                journal=journal,
                require_final=False,
            )
            self.stage = "native_handover"
            native_started = True
            await self._native.async_apply()
            self.stage = "finalize"
            await self._call(
                "async_finalize_tambur_room_migration",
                self._plan,
                journal=journal,
                on_registry=persist_registry,
            )
            after = await self._call(
                "async_capture_tambur_room_migration", self._plan
            )
            journal["after"] = copy.deepcopy(after)
            self.stage = "binding"
            await self._call(
                "async_verify_tambur_room_migration",
                self._plan,
                journal=journal,
                require_final=True,
            )
            if not await self._native.async_verify_completed():
                raise RuntimeError("room native handover changed")
            await self._call(
                "async_commit_tambur_room_migration", self._plan, journal=journal
            )
            await self._save("completed", journal)
            return "completed"
        except BaseException as error:
            if journal is not None:
                room_rolled_back = False
                rollback = getattr(
                    self._service, "async_rollback_tambur_room_migration", None
                )
                if callable(rollback):
                    try:
                        room_rolled_back = bool(
                            await asyncio.shield(
                                rollback(self._plan, journal=journal)
                            )
                        )
                    except Exception:  # noqa: BLE001
                        pass
                native_rolled_back = not native_started
                native_rollback = getattr(self._native, "async_rollback", None)
                if callable(native_rollback):
                    try:
                        native_rolled_back = bool(
                            await asyncio.shield(native_rollback())
                        )
                    except Exception:  # noqa: BLE001
                        pass
                elif native_started:
                    native_rolled_back = False
                if room_rolled_back and native_rolled_back:
                    try:
                        before = await self._call(
                            "async_capture_tambur_room_migration", self._plan
                        )
                        await self._save(
                            "prepared",
                            {
                                "before": copy.deepcopy(before),
                                "staged": None,
                                "registry": {"state": "pending"},
                                "after": None,
                            },
                        )
                    except Exception:  # noqa: BLE001
                        pass
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, TamburRoomMigrationConflict):
                raise
            raise TamburRoomMigrationConflict(self.stage) from error


class TamburRoomStartupCoordinator:
    """Wait only for Tambur inputs, migrate, then expose one room scope."""

    def __init__(
        self,
        service: object,
        migration: TamburRoomMigration,
        activate: Callable[[TamburRuntimeScope], Awaitable[object]],
        *,
        status_publisher: Callable[[dict[str, str]], None] | None = None,
    ) -> None:
        self._service = service
        self._migration = migration
        self._activate = activate
        self._publish_status = status_publisher or (lambda _status: None)
        self._remove_observer: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._cancelled = False
        self._activation_cleanup: Callable[[], None] | None = None
        self._activation_revoke: Callable[[], None] | None = None
        self.ready = False

    async def async_start(self) -> None:
        if self._started or self._cancelled:
            return
        self._started = True
        add = getattr(self._service, "add_catalog_warmup_observer", None)
        if callable(add):
            self._remove_observer = add(self._async_catalog_snapshot)
        await self._async_attempt(self._service.current_catalog(), final=False)

    async def _async_catalog_snapshot(self, catalog: object, final: bool) -> None:
        await self._async_attempt(catalog, final=final)

    async def _async_attempt(self, catalog: object, *, final: bool) -> None:
        async with self._lock:
            if self._cancelled or self.ready:
                return
            resolve = getattr(catalog, "device", None)
            available = callable(resolve) and all(
                getattr(resolve(target_id), "target_id", None) == target_id
                for target_id in TAMBUR_INPUT_TARGET_IDS
            )
            if not available:
                self._publish_status(
                    {
                        "state": "blocked" if final else "waiting",
                        "stage": "binding",
                    }
                )
                return
            try:
                await self._migration.async_apply()
                result = await self._activate(TamburRuntimeScope())
                cleanup = result if callable(result) else getattr(result, "cleanup", None)
                commit = getattr(result, "commit", None)
                revoke = getattr(result, "revoke", None)
                self._activation_cleanup = cleanup if callable(cleanup) else None
                self._activation_revoke = revoke if callable(revoke) else None
                if callable(commit):
                    commit()
            except asyncio.CancelledError:
                raise
            except TamburRoomMigrationConflict as error:
                self._publish_status({"state": "blocked", "stage": error.stage})
                self._unsubscribe()
                return
            except Exception:  # noqa: BLE001
                self._publish_status({"state": "blocked", "stage": "binding"})
                self._unsubscribe()
                return
            if self._cancelled:
                self._cleanup_activation()
                return
            self.ready = True
            self._unsubscribe()
            self._publish_status({"state": "completed", "stage": "binding"})

    def cancel(self) -> None:
        self._cancelled = True
        self.ready = False
        self._unsubscribe()
        revoke = self._activation_revoke
        self._activation_revoke = None
        if revoke is not None:
            revoke()
        self._cleanup_activation()

    def _cleanup_activation(self) -> None:
        cleanup = self._activation_cleanup
        self._activation_cleanup = None
        if cleanup is not None:
            cleanup()

    def _unsubscribe(self) -> None:
        remove = self._remove_observer
        self._remove_observer = None
        if remove is not None:
            remove()


def build_home_assistant_tambur_native_migration(
    hass: HomeAssistant, entry_id: str
) -> TamburNativeAutomationMigration:
    return TamburNativeAutomationMigration(
        HomeAssistantNativeAutomationAdapter(hass),
        HomeAssistantTamburNativeMigrationStore(hass, entry_id),
    )
