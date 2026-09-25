"""Explicit local-admin re-arm of a durable managed-switch migration receipt.

The startup migration is fail-closed: a durable ``completed`` or ``prepared``
receipt whose ``manifestHash`` no longer matches the recomputed protected
topology manifest is rejected and commands stay disabled.  Recovering from that
state must never happen automatically and must never require editing
``.storage`` by hand.

This module owns the only supported recovery path.  It reads the durable
receipt through the same Home Assistant Store used by the migration and, under
an explicit compare-and-set, replaces it with a ``superseded`` marker that
records the newly expected manifest hash.  The next startup recomputes the
manifest and only proceeds when the marker matches; otherwise it remains
blocked.  The operation is local-admin only, writes no physical command and
exposes only logical, redacted data.
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

from .managed_switch_migration import (
    MIGRATION_ID,
    MIGRATION_VERSION,
    ManagedSwitchMigrationConflict,
    _is_valid_superseded_receipt,
    _manifest_hash_for,
    _valid_hash_text,
    _valid_migration_journal,
)
from .scenario_consolidation_inventory import (
    REGISTRY_DISPOSITION_COUNTS,
    REGISTRY_DISPOSITION_HASH,
    REGISTRY_DISPOSITION_VERSION,
)

MIGRATION_REARM_CONTRACT = "hausman-hub-managed-switch-migration"
MIGRATION_REARM_REQUEST_CONTRACT = (
    "hausman-hub-managed-switch-migration-rearm-request"
)
MIGRATION_REARM_CONFIRMATION = "re-arm"

_REARMABLE_STATES = frozenset({"prepared", "completed", "superseded"})
_RECEIPT_STATES = frozenset({"prepared", "completed", "superseded"})


class ManagedSwitchMigrationRearmViolation(ValueError):
    """A re-arm request is malformed, stale or cannot be applied safely."""

    def __init__(
        self,
        message: str,
        *,
        stale: bool = False,
        unprocessable: bool = False,
    ) -> None:
        super().__init__(message)
        self.stale = stale
        self.unprocessable = unprocessable


def _timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _receipt_revision(value: object) -> int:
    if not isinstance(value, Mapping):
        return 0
    revision = value.get("revision", 0)
    return revision if type(revision) is int and revision >= 0 else 0


def _rearm_request_from_payload(payload: object) -> int:
    if not isinstance(payload, Mapping) or set(payload) != {
        "contract",
        "expectedReceiptRevision",
        "confirm",
    }:
        raise ManagedSwitchMigrationRearmViolation("re-arm body is invalid")
    if payload.get("contract") != {
        "name": MIGRATION_REARM_REQUEST_CONTRACT,
        "version": 1,
    }:
        raise ManagedSwitchMigrationRearmViolation("re-arm body is invalid")
    if payload.get("confirm") != MIGRATION_REARM_CONFIRMATION:
        raise ManagedSwitchMigrationRearmViolation("re-arm confirmation is missing")
    expected_revision = payload.get("expectedReceiptRevision")
    if type(expected_revision) is not int or expected_revision < 0:
        raise ManagedSwitchMigrationRearmViolation(
            "re-arm receipt revision is invalid"
        )
    return expected_revision


def _redacted_reason(error: object) -> str:
    message = str(error).lower()
    if "missing" in message:
        return "protected_topology_missing"
    if "incomplete" in message:
        return "protected_topology_incomplete"
    if "invalid" in message:
        return "protected_topology_invalid"
    if "unavailable" in message:
        return "protected_topology_unavailable"
    return "expectation_unavailable"


class ManagedSwitchMigrationRearmService:
    """Preview and explicitly re-arm one durable migration receipt."""

    def __init__(
        self,
        store: object,
        manifest_provider: Callable[[], object],
        *,
        now: Callable[[], datetime] | None = None,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._store = store
        self._manifest_provider = manifest_provider
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = mutation_lock or asyncio.Lock()

    async def _async_manifest(self) -> tuple[object | None, str | None]:
        try:
            entries = self._manifest_provider()
            if asyncio.iscoroutine(entries):
                entries = await entries
        except ManagedSwitchMigrationConflict as error:
            return None, _redacted_reason(error)
        except Exception:  # noqa: BLE001 - preview must stay redacted
            return None, "expectation_unavailable"
        if not isinstance(entries, tuple) or not entries:
            return None, "expectation_unavailable"
        return entries, None

    async def async_status(self) -> dict[str, object]:
        receipt = await self._store.async_load()  # type: ignore[attr-defined]
        entries, reason = await self._async_manifest()
        return self._status(receipt, entries, reason)

    async def async_rearm(self, payload: object) -> dict[str, object]:
        expected_revision = _rearm_request_from_payload(payload)
        async with self._lock:
            receipt = await self._store.async_load()  # type: ignore[attr-defined]
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("state") not in _REARMABLE_STATES
            ):
                raise ManagedSwitchMigrationRearmViolation(
                    "no durable migration receipt is available to re-arm",
                    unprocessable=True,
                )
            if _receipt_revision(receipt) != expected_revision:
                raise ManagedSwitchMigrationRearmViolation(
                    "migration receipt revision is stale",
                    stale=True,
                )
            entries, _reason = await self._async_manifest()
            if entries is None:
                raise ManagedSwitchMigrationRearmViolation(
                    "migration expectation is not usable",
                    unprocessable=True,
                )
            superseded = self._superseded_receipt(receipt, entries)
            if not _is_valid_superseded_receipt(superseded):
                raise ManagedSwitchMigrationRearmViolation(
                    "superseded receipt is invalid",
                    unprocessable=True,
                )
            try:
                await self._store.async_save(superseded)  # type: ignore[attr-defined]
            except Exception as error:  # noqa: BLE001
                raise ManagedSwitchMigrationRearmViolation(
                    "migration receipt storage failed"
                ) from error
            return self._status(superseded, entries, None)

    def _superseded_receipt(
        self, receipt: Mapping[str, object], entries: object
    ) -> dict[str, object]:
        previous_hash = receipt.get("manifestHash")
        marker: dict[str, object] = {
            "migrationId": MIGRATION_ID,
            "version": MIGRATION_VERSION,
            "state": "superseded",
            "revision": _receipt_revision(receipt) + 1,
            "manifestHash": _manifest_hash_for(entries),  # type: ignore[arg-type]
            "supersededManifestHash": (
                previous_hash if isinstance(previous_hash, str) else None
            ),
            "supersededState": receipt.get("state"),
            "supersededAt": _timestamp(self._now),
            "dispositionVersion": REGISTRY_DISPOSITION_VERSION,
            "dispositionHash": REGISTRY_DISPOSITION_HASH,
            "dispositionCounts": dict(REGISTRY_DISPOSITION_COUNTS),
        }
        # Preserve the previous completed receipt so startup can prove the
        # already-migrated registry did not drift before reconciling it under
        # the fresh manifest.  A journal that is not valid against the new
        # manifest is intentionally dropped, which keeps the reconcile path
        # fail-closed instead of guessing.
        if (
            receipt.get("state") == "completed"
            and _valid_hash_text(previous_hash)
            and _valid_migration_journal(receipt.get("journal"), entries)  # type: ignore[arg-type]
        ):
            marker["supersededReceipt"] = {
                "state": "completed",
                "manifestHash": previous_hash,
                "revision": _receipt_revision(receipt),
                "journal": copy.deepcopy(receipt.get("journal")),
            }
        return marker

    def _status(
        self,
        receipt: object,
        entries: object | None,
        reason: str | None,
    ) -> dict[str, object]:
        present = isinstance(receipt, Mapping)
        state = receipt.get("state") if present else None
        if state not in _RECEIPT_STATES:
            state = None
        manifest_hash = (
            receipt.get("manifestHash")
            if present and isinstance(receipt.get("manifestHash"), str)
            else None
        )
        superseded_manifest_hash = (
            receipt.get("supersededManifestHash")
            if present
            and isinstance(receipt.get("supersededManifestHash"), str)
            else None
        )
        superseded_state = receipt.get("supersededState") if present else None
        if superseded_state not in _RECEIPT_STATES:
            superseded_state = None
        expected_hash = (
            _manifest_hash_for(entries) if entries is not None else None  # type: ignore[arg-type]
        )
        matches: bool | None = None
        if present and expected_hash is not None:
            matches = manifest_hash == expected_hash
        operations = {"replace": 0, "create": 0}
        scenarios: list[dict[str, object]] = []
        if entries is not None:
            for item in entries:  # type: ignore[union-attr]
                operation = getattr(item, "operation", "replace")
                if operation not in operations:
                    operation = "replace"
                operations[operation] += 1
                scenarios.append(
                    {
                        "scenarioId": str(getattr(item, "scenario_id", "")),
                        "operation": operation,
                        "sourceFile": str(getattr(item, "source_file", "")),
                        "activationReady": bool(
                            getattr(item, "activation_ready", False)
                        ),
                    }
                )
        return {
            "contract": {"name": MIGRATION_REARM_CONTRACT, "version": 1},
            "receipt": {
                "present": present,
                "state": state,
                "revision": _receipt_revision(receipt),
                "manifestHash": manifest_hash,
                "supersededManifestHash": superseded_manifest_hash,
                "supersededState": superseded_state,
                "matchesExpectation": matches,
            },
            "expectation": {
                "ready": entries is not None,
                "reason": reason,
                "manifestHash": expected_hash,
                "scenarioCount": len(entries) if entries is not None else 0,
                "operations": operations,
                "scenarios": scenarios,
            },
            "physicalCommandsSent": False,
        }


__all__ = [
    "MIGRATION_REARM_CONFIRMATION",
    "MIGRATION_REARM_CONTRACT",
    "MIGRATION_REARM_REQUEST_CONTRACT",
    "ManagedSwitchMigrationRearmService",
    "ManagedSwitchMigrationRearmViolation",
]
