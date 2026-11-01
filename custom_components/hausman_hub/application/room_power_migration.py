"""Internal gate for exact power topology of a room.

This module is deliberately *not* part of the public device power dependency
contract. The durable map only describes edges; it cannot express "this target
has no upstream source" or "the room's circuit has been fully described". Until
a room is explicitly claimed here, the room keeps the legacy whole-room
``power_switch`` semantics. That makes the exact transition atomic per room:

* an empty map changes nothing for rooms without a claim;
* a claim lists every power-dependent target of the room together with its
  expected source, so a partial or damaged map is detected and blocked instead
  of guessed;
* targets of a claimed room that are absent from the claim (for example an
  independent mirror or point switches) keep their own reported state.

Only a verified physical circuit may be added to a protected runtime claim. This
public module intentionally carries no private Home Assistant entity ids; tests
use anonymized fixtures, while production topology must come from protected
runtime configuration; the live map may only verify already claimed targets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import re

from ..domain.device_power_dependencies import DevicePowerDependency

# Default public exact-topology claims. Keep this empty: real entity ids belong
# to protected runtime configuration, not to the public HACS package. A non-empty
# claim may be injected by tests or by private runtime setup. Keys may be stable
# room-light target ids (preferred, survives HA entity-id changes) or dependent
# entity ids kept for compatibility with earlier local tests.
EXACT_POWER_TARGET_MANIFEST: Mapping[str, str] = {}
ROOM_POWER_MIGRATION_CLAIM_VERSION = 1
ROOM_POWER_MIGRATION_CORRUPTION_SENTINEL = "__room_power_migration_store_corrupt__"
ROOM_POWER_MIGRATION_CLAIM_REQUEST_CONTRACT = (
    "hausman-hub-room-power-migration-claim-request"
)
ROOM_POWER_MIGRATION_CLAIM_STATUS_CONTRACT = (
    "hausman-hub-room-power-migration-claim-status"
)
_STABLE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class RoomPowerMigrationManifestError(ValueError):
    """Protected exact-power claim storage is damaged or unsupported."""


class RoomPowerMigrationStorageError(RuntimeError):
    """Protected exact-power claim storage did not durably save."""


class RoomPowerMigrationClaimViolation(ValueError):
    """A logical exact-power claim request is malformed, stale or unsafe."""

    def __init__(
        self,
        message: str,
        *,
        stale: bool = False,
        not_found: bool = False,
        unprocessable: bool = False,
    ) -> None:
        super().__init__(message)
        self.stale = stale
        self.not_found = not_found
        self.unprocessable = unprocessable


@dataclass(frozen=True, slots=True)
class ExactPowerDecision:
    """Whether one room uses the exact map and whether its claim is complete."""

    active: bool
    incomplete: bool
    global_fail_closed: bool = False
    missing: tuple[str, ...] = ()
    wrong: tuple[str, ...] = ()
    extra: tuple[str, ...] = ()
    projection: Mapping[str, DevicePowerDependency] | None = None
    claimed_target_ids: tuple[str, ...] = ()
    unproven_target_ids: tuple[str, ...] = ()

    @property
    def diagnostics(self) -> tuple[str, ...]:
        """Redacted topology problems safe for logs and receipts."""

        return tuple(
            sorted(
                set(self.missing)
                | {f"wrong:{item}" for item in self.wrong}
                | {f"extra:{item}" for item in self.extra}
            )
        )


def _target_refs(
    targets: tuple[str | None | tuple[str, str | None], ...],
) -> tuple[tuple[str | None, str | None], ...]:
    refs: list[tuple[str | None, str | None]] = []
    for item in targets:
        if isinstance(item, tuple):
            target_id, entity_id = item
            refs.append(
                (
                    target_id if isinstance(target_id, str) and target_id else None,
                    entity_id if isinstance(entity_id, str) and entity_id else None,
                )
            )
        else:
            refs.append((None, item if isinstance(item, str) and item else None))
    return tuple(refs)


def room_target_claim_key(room_id: str, target_id: str) -> str:
    """Stable private-storage key for one room-light target claim."""

    return f"room:{room_id}:target:{target_id}"


def _stable_id(value: object, label: str) -> str:
    if not isinstance(value, str) or _STABLE_ID_RE.fullmatch(value) is None:
        raise RoomPowerMigrationClaimViolation(f"{label} is invalid")
    return value


def _room_claim_prefix(room_id: str) -> str:
    return f"room:{room_id}:target:"


def _target_label(target_id: str | None) -> str:
    """Return a redacted logical target label without HA entity ids."""

    return f"target:{target_id}" if target_id else "target:unknown"


def _claim_label(key: str) -> str:
    if ":target:" in key:
        return f"claim:{key}"
    return "claim:protected"


def decide_exact_power(
    target_entity_ids: tuple[str | None | tuple[str, str | None], ...],
    dependencies: Mapping[str, DevicePowerDependency] | None,
    *,
    manifest: Mapping[str, str] = EXACT_POWER_TARGET_MANIFEST,
    room_id: str | None = None,
) -> ExactPowerDecision:
    """Resolve the per-room exact-power gate without guessing a circuit.

    ``active`` means the room must use the exact per-target map and must not
    apply the legacy whole-room power switch to every target. ``incomplete``
    means the room was explicitly claimed but the live map does not match the
    claim (or is unavailable); callers must fail closed for commands that may
    need power and diagnose the missing bindings.
    """

    refs = _target_refs(target_entity_ids)
    if ROOM_POWER_MIGRATION_CORRUPTION_SENTINEL in manifest:
        return ExactPowerDecision(
            active=True,
            incomplete=True,
            global_fail_closed=True,
            missing=("protected_power_migration_store_corrupt",),
        )
    projection: dict[str, DevicePowerDependency] = {}
    missing: set[str] = set()
    wrong: set[str] = set()
    extra: set[str] = set()
    claimed_target_ids: set[str] = set()
    unproven_target_ids: set[str] = set()
    room_claimed = False
    room_prefix = _room_claim_prefix(room_id) if room_id else None
    room_claim_keys: dict[str, str] = {}
    has_room_scoped_claims = any(
        isinstance(key, str) and key.startswith("room:") and ":target:" in key
        for key in manifest
    )
    if room_prefix is not None:
        room_claim_keys = {
            key[len(room_prefix) :]: source
            for key, source in manifest.items()
            if isinstance(key, str)
            and key.startswith(room_prefix)
            and isinstance(source, str)
            and source
        }
        room_claimed = bool(room_claim_keys)
    if manifest:
        current_target_ids = {
            target_id for target_id, _entity_id in refs if target_id is not None
        }
        for target_id in room_claim_keys:
            if target_id not in current_target_ids:
                missing.add(
                    _claim_label(
                        room_target_claim_key(str(room_id), target_id)
                        if room_id is not None
                        else target_id
                    )
                )
        for target_id, entity_id in refs:
            expected_source = None
            if (
                room_id is not None
                and target_id is not None
                and room_target_claim_key(room_id, target_id) in manifest
            ):
                expected_source = manifest[room_target_claim_key(room_id, target_id)]
            elif (
                not room_claimed
                and not has_room_scoped_claims
                and target_id is not None
                and target_id in manifest
            ):
                expected_source = manifest[target_id]
            elif (
                not room_claimed
                and not has_room_scoped_claims
                and entity_id is not None
                and entity_id in manifest
            ):
                expected_source = manifest[entity_id]
            if expected_source is None:
                continue
            if target_id is not None:
                claimed_target_ids.add(target_id)
            # A protected target claim is the only allowed edge for this target.
            # The live map may confirm that edge, but never add another target
            # to the room claim.
            label = _target_label(target_id)
            if entity_id is None:
                missing.add(label)
                if target_id is not None:
                    unproven_target_ids.add(target_id)
                continue
            dependency = dependencies.get(entity_id) if dependencies is not None else None
            if dependency is None:
                missing.add(label)
                if target_id is not None:
                    unproven_target_ids.add(target_id)
                continue
            if (
                dependency.dependent_entity_id != entity_id
                or dependency.power_source_entity_id != expected_source
            ):
                wrong.add(label)
                if target_id is not None:
                    unproven_target_ids.add(target_id)
                continue
            projection[entity_id] = dependency
        if room_claimed or claimed_target_ids:
            for target_id, entity_id in refs:
                if entity_id is None:
                    continue
                if target_id in claimed_target_ids:
                    continue
                if dependencies is not None and entity_id in dependencies:
                    extra.add(_target_label(target_id))
                    if target_id is not None:
                        unproven_target_ids.add(target_id)
    if not (room_claimed or claimed_target_ids):
        # No explicit migration claim: the legacy whole-room association stays
        # in force, even when a (possibly partial) map exists.
        return ExactPowerDecision(active=False, incomplete=False)
    missing_tuple = tuple(sorted(missing))
    wrong_tuple = tuple(sorted(wrong))
    extra_tuple = tuple(sorted(extra))
    return ExactPowerDecision(
        active=True,
        incomplete=bool(missing_tuple or wrong_tuple or extra_tuple),
        missing=missing_tuple,
        wrong=wrong_tuple,
        extra=extra_tuple,
        projection=projection,
        claimed_target_ids=tuple(sorted(claimed_target_ids)),
        unproven_target_ids=tuple(sorted(unproven_target_ids)),
    )


def manifest_from_payload(payload: object) -> dict[str, str]:
    """Load protected exact-power claims from local runtime storage."""

    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise RoomPowerMigrationManifestError("room power migration store is malformed")
    if payload.get("version") != ROOM_POWER_MIGRATION_CLAIM_VERSION:
        raise RoomPowerMigrationManifestError("room power migration store version is unsupported")
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise RoomPowerMigrationManifestError("room power migration claims are malformed")
    manifest: dict[str, str] = {}
    for raw in claims:
        if not isinstance(raw, Mapping):
            raise RoomPowerMigrationManifestError("room power migration claim is malformed")
        key = raw.get("key")
        source = raw.get("sourceEntityId")
        if not (isinstance(key, str) and key and isinstance(source, str) and source):
            raise RoomPowerMigrationManifestError("room power migration claim is malformed")
        manifest[key] = source
    return manifest


def corrupt_manifest() -> dict[str, str]:
    """Return a redacted fail-closed manifest for damaged protected storage."""

    return {
        ROOM_POWER_MIGRATION_CORRUPTION_SENTINEL: ROOM_POWER_MIGRATION_CORRUPTION_SENTINEL,
    }


def manifest_to_payload(manifest: Mapping[str, str]) -> dict[str, object]:
    """Persist exact-power claims without publishing any private identifiers."""

    return {
        "version": ROOM_POWER_MIGRATION_CLAIM_VERSION,
        "claims": [
            {"key": key, "sourceEntityId": source}
            for key, source in sorted(manifest.items())
            if isinstance(key, str)
            and key
            and isinstance(source, str)
            and source
        ],
    }


def _manifest_payload(
    manifest: Mapping[str, str],
    *,
    revision: int,
    updated_at: str,
) -> dict[str, object]:
    payload = manifest_to_payload(manifest)
    payload["revision"] = revision
    payload["updatedAt"] = updated_at
    return payload


def _manifest_revision_from_payload(payload: object) -> int:
    if payload is None:
        return 0
    if not isinstance(payload, Mapping):
        raise RoomPowerMigrationManifestError("room power migration store is malformed")
    revision = payload.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise RoomPowerMigrationManifestError(
            "room power migration store revision is malformed"
        )
    return revision


def _manifest_updated_at_from_payload(payload: object) -> str | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise RoomPowerMigrationManifestError("room power migration store is malformed")
    updated_at = payload.get("updatedAt")
    if updated_at is None:
        return None
    if not isinstance(updated_at, str) or not updated_at:
        raise RoomPowerMigrationManifestError(
            "room power migration store timestamp is malformed"
        )
    return updated_at


def _timestamp(now: Callable[[], datetime] | None = None) -> str:
    value = (now or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class _ClaimRequest:
    room_id: str
    target_ids: tuple[str, ...]
    expected_room_config_version: int
    expected_dependency_revision: int
    expected_claim_revision: int


def _claim_request_from_payload(
    payload: object,
    *,
    path_room_id: str | None = None,
) -> _ClaimRequest:
    if not isinstance(payload, Mapping) or set(payload) != {
        "contract",
        "roomId",
        "targetIds",
        "expectedRoomConfigVersion",
        "expectedDependencyRevision",
        "expectedClaimRevision",
    }:
        raise RoomPowerMigrationClaimViolation("room power migration claim body is invalid")
    contract = payload.get("contract")
    if not isinstance(contract, Mapping) or contract != {
        "name": ROOM_POWER_MIGRATION_CLAIM_REQUEST_CONTRACT,
        "version": 1,
    }:
        raise RoomPowerMigrationClaimViolation("room power migration claim contract is invalid")
    room_id = _stable_id(payload.get("roomId"), "room id")
    if path_room_id is not None and room_id != path_room_id:
        raise RoomPowerMigrationClaimViolation("room id does not match the path")
    raw_targets = payload.get("targetIds")
    if not isinstance(raw_targets, Sequence) or isinstance(raw_targets, (str, bytes)):
        raise RoomPowerMigrationClaimViolation("target ids are invalid")
    target_ids = tuple(_stable_id(item, "target id") for item in raw_targets)
    if not target_ids or len(target_ids) > 64 or len(set(target_ids)) != len(target_ids):
        raise RoomPowerMigrationClaimViolation("target ids are invalid")
    room_version = payload.get("expectedRoomConfigVersion")
    dependency_revision = payload.get("expectedDependencyRevision")
    claim_revision = payload.get("expectedClaimRevision")
    if type(room_version) is not int or room_version < 1:
        raise RoomPowerMigrationClaimViolation("room config version is invalid")
    if type(dependency_revision) is not int or dependency_revision < 0:
        raise RoomPowerMigrationClaimViolation("dependency revision is invalid")
    if type(claim_revision) is not int or claim_revision < 0:
        raise RoomPowerMigrationClaimViolation("claim revision is invalid")
    return _ClaimRequest(
        room_id=room_id,
        target_ids=target_ids,
        expected_room_config_version=room_version,
        expected_dependency_revision=dependency_revision,
        expected_claim_revision=claim_revision,
    )


def _target_ids_from_labels(labels: Iterable[str]) -> tuple[str, ...]:
    prefix = "target:"
    values: set[str] = set()
    for label in labels:
        if isinstance(label, str) and label.startswith(prefix):
            target_id = label[len(prefix) :]
            if target_id:
                values.add(target_id)
    return tuple(sorted(values))


class RoomPowerMigrationClaimService:
    """Own the protected exact-power claim store behind a logical-only API."""

    def __init__(
        self,
        store: object,
        room_lighting_service: object,
        dependency_service: object,
        *,
        now: Callable[[], datetime] | None = None,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._store = store
        self._room_lighting_service = room_lighting_service
        self._dependency_service = dependency_service
        self._now = now
        self._manifest: dict[str, str] = {}
        self._revision = 0
        self._updated_at = _timestamp(self._now)
        self._loaded = False
        self._lock = mutation_lock or asyncio.Lock()

    async def async_load(self) -> None:
        payload = await self._store.async_load()  # type: ignore[attr-defined]
        manifest = manifest_from_payload(payload)
        revision = _manifest_revision_from_payload(payload)
        updated_at = _manifest_updated_at_from_payload(payload) or _timestamp(self._now)
        self._manifest = dict(manifest)
        self._revision = revision
        self._updated_at = updated_at
        self._loaded = True

    @property
    def manifest(self) -> dict[str, str]:
        self._require_loaded()
        return dict(self._manifest)

    @property
    def revision(self) -> int:
        self._require_loaded()
        return self._revision

    async def async_merge_live_dependencies(
        self,
        configs: Iterable[object],
        dependencies: Mapping[str, DevicePowerDependency] | None,
    ) -> dict[str, str]:
        self._require_loaded()
        async with self._lock:
            merged = merge_manifest_from_live_dependencies(
                configs,
                dependencies,
                existing=self._manifest,
            )
            if merged != self._manifest:
                await self._save_locked(merged)
            return dict(self._manifest)

    async def async_get_status(self, room_id: object) -> dict[str, object]:
        self._require_loaded()
        room = _stable_id(room_id, "room id")
        config = await self._room_config(room)
        dependencies, dependency_revision = self._dependencies()
        return self._status(
            config,
            dependencies,
            dependency_revision,
            self._manifest,
            saved=True,
        )

    async def async_preview(self, payload: object) -> dict[str, object]:
        self._require_loaded()
        request = _claim_request_from_payload(payload)
        config = await self._room_config(request.room_id)
        dependencies, dependency_revision = self._dependencies()
        self._check_cas(request, config, dependency_revision)
        candidate = self._candidate_manifest(config, dependencies, request.target_ids)
        return self._status(
            config,
            dependencies,
            dependency_revision,
            candidate,
            saved=False,
            requested_target_ids=request.target_ids,
        )

    async def async_save(self, room_id: object, payload: object) -> dict[str, object]:
        self._require_loaded()
        path_room_id = _stable_id(room_id, "room id")
        request = _claim_request_from_payload(payload, path_room_id=path_room_id)
        async with self._lock:
            # Repeat every CAS read inside the claim-store lock immediately before
            # the durable save. Preview/read never call this mutating path.
            config = await self._room_config(request.room_id)
            dependencies, dependency_revision = self._dependencies()
            self._check_cas(request, config, dependency_revision)
            candidate = self._candidate_manifest(config, dependencies, request.target_ids)
            status = self._status(
                config,
                dependencies,
                dependency_revision,
                candidate,
                saved=True,
                requested_target_ids=request.target_ids,
            )
            if not status["claimedTargetIds"]:
                raise RoomPowerMigrationClaimViolation(
                    "claim has no power-dependent targets",
                    unprocessable=True,
                )
            await self._save_locked(candidate)
            status["claimRevision"] = self._revision
            status["updatedAt"] = self._updated_at
            return status

    async def _save_locked(self, manifest: Mapping[str, str]) -> None:
        next_revision = self._revision + 1
        updated_at = _timestamp(self._now)
        saved = await self._store.async_save(  # type: ignore[attr-defined]
            _manifest_payload(manifest, revision=next_revision, updated_at=updated_at)
        )
        if saved is False:
            raise RoomPowerMigrationStorageError(
                "room power migration claim storage save failed"
            )
        self._manifest = dict(manifest)
        self._revision = next_revision
        self._updated_at = updated_at

    async def _room_config(self, room_id: str) -> object:
        config = await self._room_lighting_service.async_get_config(room_id)  # type: ignore[attr-defined]
        if config is None:
            raise RoomPowerMigrationClaimViolation(
                "room lighting config is missing",
                not_found=True,
            )
        return config

    def _dependencies(self) -> tuple[Mapping[str, DevicePowerDependency], int]:
        document = self._dependency_service.document  # type: ignore[attr-defined]
        revision = document.get("revision") if isinstance(document, Mapping) else None
        if type(revision) is not int or revision < 0:
            raise RoomPowerMigrationClaimViolation("dependency revision is unavailable")
        mapping = self._dependency_service.mapping  # type: ignore[attr-defined]
        if not isinstance(mapping, Mapping):
            raise RoomPowerMigrationClaimViolation("dependency map is unavailable")
        return mapping, revision

    def _check_cas(
        self,
        request: _ClaimRequest,
        config: object,
        dependency_revision: int,
    ) -> None:
        if getattr(config, "version", None) != request.expected_room_config_version:
            raise RoomPowerMigrationClaimViolation("room config revision is stale", stale=True)
        if dependency_revision != request.expected_dependency_revision:
            raise RoomPowerMigrationClaimViolation("dependency revision is stale", stale=True)
        if self._revision != request.expected_claim_revision:
            raise RoomPowerMigrationClaimViolation("claim revision is stale", stale=True)

    def _candidate_manifest(
        self,
        config: object,
        dependencies: Mapping[str, DevicePowerDependency],
        target_ids: Iterable[str],
    ) -> dict[str, str]:
        room_id = str(getattr(config, "room_id", ""))
        targets = {
            target.id: target
            for target in getattr(getattr(config, "devices", None), "light_targets", ())
            if isinstance(getattr(target, "id", None), str)
        }
        unknown = sorted(set(target_ids) - set(targets))
        if unknown:
            raise RoomPowerMigrationClaimViolation(
                "claim references unknown logical targets",
                unprocessable=True,
            )
        remove_keys = {
            key
            for key in self._manifest
            if isinstance(key, str) and key.startswith(_room_claim_prefix(room_id))
        }
        for target in targets.values():
            entity_id = getattr(target, "entity_id", None)
            if isinstance(entity_id, str) and entity_id:
                remove_keys.add(entity_id)
            remove_keys.add(str(target.id))
        candidate = {
            key: source
            for key, source in self._manifest.items()
            if key not in remove_keys
        }
        for target_id in sorted(set(target_ids)):
            target = targets[target_id]
            entity_id = getattr(target, "entity_id", None)
            dependency = dependencies.get(entity_id) if isinstance(entity_id, str) else None
            if dependency is None:
                continue
            candidate[room_target_claim_key(room_id, target_id)] = (
                dependency.power_source_entity_id
            )
        return candidate

    def _status(
        self,
        config: object,
        dependencies: Mapping[str, DevicePowerDependency],
        dependency_revision: int,
        manifest: Mapping[str, str],
        *,
        saved: bool,
        requested_target_ids: Iterable[str] | None = None,
    ) -> dict[str, object]:
        room_id = str(getattr(config, "room_id", ""))
        target_refs = tuple(
            (getattr(target, "id", None), getattr(target, "entity_id", None))
            for target in getattr(getattr(config, "devices", None), "light_targets", ())
        )
        targets = {
            target_id: entity_id
            for target_id, entity_id in target_refs
            if isinstance(target_id, str) and target_id
        }
        scope = set(requested_target_ids) if requested_target_ids is not None else set(targets)
        claimable = tuple(
            sorted(
                target_id
                for target_id, entity_id in targets.items()
                if target_id in scope
                and isinstance(entity_id, str)
                and entity_id in dependencies
            )
        )
        independent = tuple(sorted(target_id for target_id in scope if target_id in targets and target_id not in claimable))
        decision = decide_exact_power(
            target_refs,
            dependencies,
            manifest=manifest,
            room_id=room_id,
        )
        prefix = _room_claim_prefix(room_id)
        claimed = tuple(
            sorted(
                key[len(prefix) :]
                for key in manifest
                if isinstance(key, str)
                and key.startswith(prefix)
                and key[len(prefix) :] in targets
            )
        )
        payload: dict[str, object] = {
            "contract": {
                "name": ROOM_POWER_MIGRATION_CLAIM_STATUS_CONTRACT,
                "version": 1,
            },
            "roomId": room_id,
            "roomConfigVersion": int(getattr(config, "version")),
            "dependencyRevision": dependency_revision,
            "claimRevision": self._revision,
            "active": decision.active,
            "complete": not decision.incomplete,
            "ready": bool(claimed) and decision.active and not decision.incomplete,
            "reloadRequired": False,
            "saved": saved,
            "physicalCommandsSent": False,
            "claimableTargetIds": list(claimable),
            "claimedTargetIds": list(claimed),
            "independentTargetIds": list(independent),
            "unprovenTargetIds": list(decision.unproven_target_ids),
            "extraTargetIds": list(_target_ids_from_labels(decision.extra)),
            "diagnostics": list(decision.diagnostics),
            "updatedAt": self._updated_at,
        }
        if requested_target_ids is not None:
            payload["requestedTargetIds"] = list(requested_target_ids)
        return payload

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise RoomPowerMigrationClaimViolation(
                "room power migration claim service is not loaded"
            )


def merge_manifest_from_live_dependencies(
    configs: Iterable[object],
    dependencies: Mapping[str, DevicePowerDependency] | None,
    *,
    existing: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return protected claims verified or enriched by the live map.

    A live dependency edge never creates a migration claim by itself. It may only
    add the current entity-id alias for an already protected canonical
    room/target claim, so partial maps cannot silently move a legacy room into
    exact mode or copy a legacy plain alias to another room with the same target
    id.
    """

    manifest = {
        str(key): str(source)
        for key, source in (existing or {}).items()
        if isinstance(key, str)
        and key
        and isinstance(source, str)
        and source
    }
    if dependencies is None or not manifest:
        return manifest
    for config in configs:
        room_id = getattr(config, "room_id", None)
        devices = getattr(config, "devices", None)
        targets = getattr(devices, "light_targets", ())
        if not isinstance(room_id, str) or not room_id:
            continue
        for target in targets:
            target_id = getattr(target, "id", None)
            entity_id = getattr(target, "entity_id", None)
            if not isinstance(target_id, str) or not target_id:
                continue
            if not isinstance(entity_id, str) or not entity_id:
                continue
            room_key = room_target_claim_key(room_id, target_id)
            expected = manifest.get(room_key)
            if not isinstance(expected, str) or not expected:
                continue
            dependency = dependencies.get(entity_id)
            if dependency is None:
                continue
            source = dependency.power_source_entity_id
            if not isinstance(source, str) or not source or source != expected:
                continue
            manifest[room_key] = source
            manifest[entity_id] = source
    return manifest


__all__ = [
    "EXACT_POWER_TARGET_MANIFEST",
    "ExactPowerDecision",
    "ROOM_POWER_MIGRATION_CLAIM_VERSION",
    "ROOM_POWER_MIGRATION_CORRUPTION_SENTINEL",
    "RoomPowerMigrationManifestError",
    "RoomPowerMigrationStorageError",
    "RoomPowerMigrationClaimService",
    "RoomPowerMigrationClaimViolation",
    "corrupt_manifest",
    "decide_exact_power",
    "manifest_from_payload",
    "manifest_to_payload",
    "merge_manifest_from_live_dependencies",
    "room_target_claim_key",
]
