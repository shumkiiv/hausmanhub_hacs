"""Per-install protected topology roles.

The public package must not ship Home Assistant entity ids nor catalog
fingerprints derived from them.  Runtime code therefore speaks only in logical
roles (for example ``managed.shower.cabinet``) and resolves them from a local
Home Assistant ``.storage`` document or from already protected configuration.
API/readiness responses intentionally expose only role names and booleans.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any

from .scenario_catalog import _stable_target_id_from_entity


PROTECTED_TOPOLOGY_VERSION = 1
PROTECTED_TOPOLOGY_CONTRACT = "hausman-hub-protected-topology"
PROTECTED_TOPOLOGY_REPLACE_CONTRACT = "hausman-hub-protected-topology-replace-request"
PROTECTED_TOPOLOGY_READINESS_CONTRACT = "hausman-hub-protected-topology-readiness-request"

ROLE_STORAGE_MOTION = "managed.storage.motion"
ROLE_STORAGE_LIGHT = "managed.storage.light"
ROLE_STORAGE_EXHAUST = "managed.storage.exhaust"
ROLE_TAMBUR_MOTION = "managed.tambur.motion"
ROLE_TAMBUR_PRESENCE_PRIMARY = "managed.tambur.presence.primary"
ROLE_TAMBUR_PRESENCE_SECONDARY = "managed.tambur.presence.secondary"
ROLE_TAMBUR_CHANDELIER = "managed.tambur.chandelier"
ROLE_TAMBUR_POINTS = "managed.tambur.points"
ROLE_TAMBUR_MIRROR = "managed.tambur.mirror"
ROLE_TAMBUR_POWER = "managed.tambur.power"
ROLE_TAMBUR_ENTRY = "managed.tambur.entry"
ROLE_SMALL_MOTION = "managed.small_corridor.motion"
ROLE_SMALL_LUX = "managed.small_corridor.lux"
ROLE_SMALL_RELAY = "managed.small_corridor.relay"
ROLE_SMALL_CHANDELIER = "managed.small_corridor.chandelier"
ROLE_SHARED_SUN = "managed.shared.sun"
ROLE_SHOWER_PRESENCE = "managed.shower.presence"
ROLE_SHOWER_HUMIDITY = "managed.shower.humidity"
ROLE_SHOWER_MAIN = "managed.shower.main"
ROLE_SHOWER_EXTRA = "managed.shower.extra"
ROLE_SHOWER_FAN = "managed.shower.fan"
ROLE_SHOWER_CABINET = "managed.shower.cabinet"
ROLE_TOILET_MOTION_PRIMARY = "managed.toilet.motion.primary"
ROLE_TOILET_MOTION_SECONDARY = "managed.toilet.motion.secondary"
ROLE_TOILET_MAIN = "managed.toilet.main"
ROLE_TOILET_NIGHT = "managed.toilet.night"
ROLE_TOILET_FAN = "managed.toilet.fan"
ROLE_TOILET_AWAY = "managed.toilet.away"
ROLE_BATHROOM_LIGHT_PRIMARY = "managed.bathroom.light.primary"
ROLE_BATHROOM_LIGHT_SECONDARY = "managed.bathroom.light.secondary"
ROLE_BATHROOM_HUMIDITY = "managed.bathroom.humidity"
ROLE_BATHROOM_FAN = "managed.bathroom.fan"
ROLE_OFFICE_LIGHT = "managed.office.light"
ROLE_OFFICE_RELAY = "managed.office.relay"
ROLE_OFFICE_LUX = "managed.office.lux"
ROLE_CURTAIN_LIVING = "managed.curtain.living"
ROLE_CURTAIN_KITCHEN = "managed.curtain.kitchen"
ROLE_CURTAIN_ROOM = "managed.curtain.room"
ROLE_CURTAIN_OFFICE = "managed.curtain.office"
ROLE_AWAY_SENSOR = "managed.away.sensor"
# Compatibility alias for drafts/tests that used the voice-specific name.  The
# canonical binding is shared by room lighting and voice greeting so production
# setup resolves it once from protected topology instead of shipping an entity id.
ROLE_VOICE_AWAY = ROLE_AWAY_SENSOR

CURTAIN_ROLES = (
    ROLE_CURTAIN_LIVING,
    ROLE_CURTAIN_KITCHEN,
    ROLE_CURTAIN_ROOM,
    ROLE_CURTAIN_OFFICE,
)

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){1,7}$")
_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_MANAGED_SWITCH_SOURCE_FILES = frozenset({
    "shower_controller.js",
    "small_corridor_controller.js",
    "tambur_controller.js",
    "toilet_controller.js",
    "bathroom_controller.js",
    "storage_controller.js",
    "cabinet_controller.js",
    "curtains_controller.js",
})
_MIGRATION_EXPECTATION_KEYS = {
    "nativeAutomations",
    "managedSwitches",
    "managedSwitchBindings",
}


class ProtectedTopologyViolation(ValueError):
    """A protected topology write is malformed, stale or unsafe."""

    def __init__(self, message: str, *, stale: bool = False, storage: bool = False) -> None:
        super().__init__(message)
        self.stale = stale
        self.storage = storage


class ProtectedTopologyStorageError(RuntimeError):
    """Protected topology storage could not be read or saved."""


@dataclass(frozen=True, slots=True)
class ProtectedRoleBinding:
    role: str
    target_id: str | None = None
    entity_id: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"role": self.role}
        if self.target_id is not None:
            payload["targetId"] = self.target_id
        if self.entity_id is not None:
            payload["entityId"] = self.entity_id
        return payload


def _valid_role(value: object) -> str:
    if not isinstance(value, str) or _ROLE_RE.fullmatch(value) is None:
        raise ProtectedTopologyViolation("protected topology role is invalid")
    return value


def _valid_target(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _TARGET_RE.fullmatch(value) is None:
        raise ProtectedTopologyViolation("protected topology target is invalid")
    return value


def _required_target(value: object, label: str) -> str:
    target = _valid_target(value)
    if target is None:
        raise ProtectedTopologyViolation(f"protected topology {label} is invalid")
    return target


def _valid_entity(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _ENTITY_RE.fullmatch(value) is None:
        raise ProtectedTopologyViolation("protected topology entity is invalid")
    return value


def _valid_hash(value: object) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ProtectedTopologyViolation("protected topology hash is invalid")
    return value


def _valid_source_file(value: object) -> str:
    if not isinstance(value, str) or value not in _RELEASE_MANAGED_SWITCH_SOURCE_FILES:
        raise ProtectedTopologyViolation("protected topology source file is invalid")
    return value


def _valid_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ProtectedTopologyViolation(f"protected topology {label} is invalid")
    return value


def _valid_int(value: object, label: str, *, nullable: bool = False) -> int | None:
    if value is None and nullable:
        return None
    if type(value) is not int or value < 0:
        raise ProtectedTopologyViolation(f"protected topology {label} is invalid")
    return value


def _valid_role_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise ProtectedTopologyViolation("protected topology role list is invalid")
    roles = tuple(_valid_role(item) for item in value)
    if len(roles) != len(set(roles)):
        raise ProtectedTopologyViolation("protected topology role list is duplicated")
    return roles


def _binding_from_payload(value: object) -> ProtectedRoleBinding:
    if not isinstance(value, Mapping) or set(value) - {"role", "targetId", "entityId"}:
        raise ProtectedTopologyViolation("protected topology binding is invalid")
    role = _valid_role(value.get("role"))
    target_id = _valid_target(value.get("targetId"))
    entity_id = _valid_entity(value.get("entityId"))
    # A role has one authoritative identity.  Accepting both fields, even when
    # they currently describe the same device, creates a second identity that
    # can drift between persistence, the catalog and a later migration.
    if (target_id is None) == (entity_id is None):
        raise ProtectedTopologyViolation("protected topology binding identity is invalid")
    return ProtectedRoleBinding(role, target_id, entity_id)


def _native_expectation_from_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "role", "automationId", "state", "definitionHash", "disposition",
    }:
        raise ProtectedTopologyViolation("protected native automation expectation is invalid")
    state = value.get("state")
    disposition = value.get("disposition")
    if state not in {"on", "off"} or disposition not in {"disable", "preserve"}:
        raise ProtectedTopologyViolation("protected native automation expectation is invalid")
    return {
        "role": _valid_role(value.get("role")),
        "automationId": _required_target(value.get("automationId"), "automation id"),
        "state": state,
        "definitionHash": _valid_hash(value.get("definitionHash")),
        "disposition": disposition,
    }


def _managed_switch_expectation_from_payload(value: object) -> dict[str, object]:
    required = {
        "scenarioId", "operation", "legacyRevision", "legacySourceHash",
        "legacyTopology", "legacyInputRoles", "inputRoles", "newSourceHash",
        "sourceFile", "activationReady",
    }
    optional = {"expectedRevision"}
    if not isinstance(value, Mapping) or not required <= set(value) or set(value) - required - optional:
        raise ProtectedTopologyViolation("protected managed switch expectation is invalid")
    operation = value.get("operation")
    if operation not in {"replace", "create"}:
        raise ProtectedTopologyViolation("protected managed switch operation is invalid")
    return {
        "scenarioId": _required_target(value.get("scenarioId"), "scenario id"),
        "operation": operation,
        "expectedRevision": _valid_int(value.get("expectedRevision"), "expected revision", nullable=True),
        "legacyRevision": _valid_int(value.get("legacyRevision"), "legacy revision"),
        "legacySourceHash": _valid_hash(value.get("legacySourceHash")),
        "legacyTopology": _required_target(value.get("legacyTopology"), "legacy topology"),
        "legacyInputRoles": _valid_role_list(value.get("legacyInputRoles")),
        "inputRoles": _valid_role_list(value.get("inputRoles")),
        "newSourceHash": _valid_hash(value.get("newSourceHash")),
        "sourceFile": _valid_source_file(value.get("sourceFile")),
        "activationReady": _valid_bool(value.get("activationReady"), "activation readiness"),
    }


def _managed_switch_binding_expectation_from_payload(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {
        "scenarioId", "expectedRevision", "sourceHash", "topology", "inputRoles",
        "triggerId", "oldTargetRole", "newTargetRole", "newTargetName",
    }:
        raise ProtectedTopologyViolation("protected managed switch binding expectation is invalid")
    name = value.get("newTargetName")
    if not isinstance(name, str) or not 1 <= len(name) <= 120:
        raise ProtectedTopologyViolation("protected managed switch binding name is invalid")
    return {
        "scenarioId": _required_target(value.get("scenarioId"), "scenario id"),
        "expectedRevision": _valid_int(value.get("expectedRevision"), "expected revision"),
        "sourceHash": _valid_hash(value.get("sourceHash")),
        "topology": _required_target(value.get("topology"), "topology"),
        "inputRoles": _valid_role_list(value.get("inputRoles")),
        "triggerId": _required_target(value.get("triggerId"), "trigger id"),
        "oldTargetRole": _valid_role(value.get("oldTargetRole")),
        "newTargetRole": _valid_role(value.get("newTargetRole")),
        "newTargetName": name,
    }


def _migration_expectations_from_payload(value: object | None) -> dict[str, tuple[dict[str, object], ...]]:
    if value is None:
        return {key: () for key in _MIGRATION_EXPECTATION_KEYS}
    if not isinstance(value, Mapping) or set(value) - _MIGRATION_EXPECTATION_KEYS:
        raise ProtectedTopologyViolation("protected topology migration expectations are invalid")
    parsers = {
        "nativeAutomations": _native_expectation_from_payload,
        "managedSwitches": _managed_switch_expectation_from_payload,
        "managedSwitchBindings": _managed_switch_binding_expectation_from_payload,
    }
    result: dict[str, tuple[dict[str, object], ...]] = {}
    for key, parser in parsers.items():
        raw_items = value.get(key, [])
        if not isinstance(raw_items, list) or len(raw_items) > 64:
            raise ProtectedTopologyViolation("protected topology migration expectations are invalid")
        parsed = tuple(parser(item) for item in raw_items)
        identities: list[object] = []
        for item in parsed:
            identities.append(item.get("role", item.get("scenarioId")))
        if len(identities) != len(set(identities)):
            raise ProtectedTopologyViolation("protected topology migration expectation is duplicated")
        result[key] = parsed
    return result


def _migration_expectations_to_payload(
    expectations: Mapping[str, tuple[dict[str, object], ...]]
) -> dict[str, list[dict[str, object]]]:
    payload: dict[str, list[dict[str, object]]] = {}
    for key in sorted(_MIGRATION_EXPECTATION_KEYS):
        values = expectations.get(key, ())
        if values:
            payload[key] = [dict(item) for item in values]
    return payload


def _timestamp(now: Callable[[], datetime]) -> str:
    value = now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class ProtectedTopologyService:
    """CAS-protected local role resolver with redacted readiness reporting."""

    def __init__(
        self,
        store: object,
        *,
        catalog_resolver: Callable[[str], object | None] | None = None,
        entity_validator: Callable[[str], bool] | None = None,
        now: Callable[[], datetime] | None = None,
        mutation_lock: asyncio.Lock | None = None,
    ) -> None:
        self._store = store
        self._catalog_resolver = catalog_resolver
        self._entity_validator = entity_validator
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = mutation_lock or asyncio.Lock()
        self._revision = 0
        self._updated_at = ""
        self._bindings: dict[str, ProtectedRoleBinding] = {}
        self._migration_expectations: dict[str, tuple[dict[str, object], ...]] = {
            key: () for key in _MIGRATION_EXPECTATION_KEYS
        }
        self._loaded = False
        self._healthy = True

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def revision(self) -> int:
        self._require_loaded()
        return self._revision

    async def async_load(self) -> None:
        async with self._lock:
            # Until every stored value and runtime callback has been checked, no
            # old binding may remain usable after a reload attempt. Sharing the
            # write lock prevents this authoritative read from racing a save.
            self._healthy = False
            try:
                payload = await self._store.async_load()
            except Exception as error:  # noqa: BLE001
                self._clear_unhealthy_state()
                self._loaded = True
                raise ProtectedTopologyStorageError("protected topology storage unavailable") from error
            if payload is None:
                self._revision = 0
                self._updated_at = _timestamp(self._now)
                self._bindings = {}
                self._migration_expectations = {
                    key: () for key in _MIGRATION_EXPECTATION_KEYS
                }
                self._loaded = True
                self._healthy = True
                return
            try:
                self._load_payload(payload)
            except Exception as error:  # noqa: BLE001
                # Catalog and entity callbacks cross the Home Assistant boundary.
                # Their failures must not leave a partially initialized resolver
                # available to a later migration attempt.
                self._clear_unhealthy_state()
                self._loaded = True
                raise ProtectedTopologyStorageError("protected topology storage invalid") from error
            self._loaded = True
            self._healthy = True

    def _load_payload(self, payload: object) -> None:
        if not isinstance(payload, Mapping) or not {
            "version", "revision", "updatedAt", "bindings",
        } <= set(payload) or set(payload) - {
            "version", "revision", "updatedAt", "bindings", "migrationExpectations",
        }:
            raise ProtectedTopologyViolation("protected topology document is invalid")
        if payload.get("version") != PROTECTED_TOPOLOGY_VERSION:
            raise ProtectedTopologyViolation("protected topology version is unsupported")
        revision = payload.get("revision")
        updated_at = payload.get("updatedAt")
        bindings = payload.get("bindings")
        if type(revision) is not int or revision < 0 or not isinstance(updated_at, str):
            raise ProtectedTopologyViolation("protected topology metadata is invalid")
        if not isinstance(bindings, list) or len(bindings) > 128:
            raise ProtectedTopologyViolation("protected topology bindings are invalid")
        parsed: dict[str, ProtectedRoleBinding] = {}
        for item in bindings:
            binding = _binding_from_payload(item)
            if binding.role in parsed:
                raise ProtectedTopologyViolation("protected topology role is duplicated")
            parsed[binding.role] = binding
        # Parse and validate all independently supplied state before replacing
        # the live resolver.  A malformed expectation must never leave fresh
        # bindings paired with stale migration expectations.
        expectations = _migration_expectations_from_payload(
            payload.get("migrationExpectations")
        )
        self._validate_runtime_bindings(list(parsed.values()))
        self._revision = revision
        self._updated_at = updated_at
        self._bindings = parsed
        self._migration_expectations = expectations

    @property
    def payload(self) -> dict[str, object]:
        self._require_loaded()
        result: dict[str, object] = {
            "version": PROTECTED_TOPOLOGY_VERSION,
            "revision": self._revision,
            "updatedAt": self._updated_at,
            "bindings": [
                self._bindings[role].to_payload()
                for role in sorted(self._bindings)
            ],
        }
        expectations = _migration_expectations_to_payload(self._migration_expectations)
        if expectations:
            result["migrationExpectations"] = expectations
        return result

    def migration_expectations(self, kind: str) -> tuple[dict[str, object], ...]:
        self._require_loaded()
        if not self._healthy:
            return ()
        if kind not in _MIGRATION_EXPECTATION_KEYS:
            return ()
        return tuple(deepcopy(item) for item in self._migration_expectations.get(kind, ()))

    def target_id(self, role: str) -> str | None:
        self._require_loaded()
        if not self._healthy:
            return None
        if role == ROLE_SHARED_SUN:
            return self._target_from_public_entity("sun.sun")
        binding = self._bindings.get(role)
        if binding is None:
            return None
        if binding.target_id is not None:
            return self._resolved_target(binding.target_id)
        if binding.entity_id is not None:
            return self._target_from_public_entity(binding.entity_id)
        return None

    def entity_id(self, role: str) -> str | None:
        self._require_loaded()
        if not self._healthy:
            return None
        binding = self._bindings.get(role)
        if binding is not None and binding.entity_id is not None:
            return (
                binding.entity_id
                if self._validated_entity(binding.entity_id)
                else None
            )
        target_id = self.target_id(role)
        device = self._catalog_device(target_id) if isinstance(target_id, str) else None
        entity_id = getattr(device, "entity_id", None)
        return entity_id if isinstance(entity_id, str) and entity_id else None

    def _target_from_public_entity(self, entity_id: str) -> str | None:
        target_id = _stable_target_id_from_entity(entity_id)
        return self._resolved_target(target_id)

    def targets_for_roles(self, roles: tuple[str, ...]) -> tuple[str, ...]:
        self._require_loaded()
        if not self._healthy:
            return ()
        values: list[str] = []
        for role in roles:
            target_id = self.target_id(role)
            if target_id is None:
                return ()
            values.append(target_id)
        return tuple(values)

    def _role_readiness(self, role: str) -> dict[str, object]:
        self._require_loaded()
        if not self._healthy:
            return {"role": role, "configured": False, "missingReason": "storage_unhealthy"}
        binding = self._bindings.get(role)
        if role == ROLE_SHARED_SUN:
            return {"role": role, "configured": True, "missingReason": None}
        if binding is None:
            return {"role": role, "configured": False, "missingReason": "role_missing"}
        if binding.target_id is not None and self.target_id(role) is None:
            return {"role": role, "configured": False, "missingReason": "target_unresolved"}
        if binding.entity_id is not None and self.entity_id(role) is None:
            return {"role": role, "configured": False, "missingReason": "entity_unavailable"}
        return {"role": role, "configured": True, "missingReason": None}

    def readiness(self, required_roles: tuple[str, ...]) -> dict[str, object]:
        self._require_loaded()
        roles = [self._role_readiness(role) for role in sorted(set(required_roles))]
        return {
            "contract": {"name": PROTECTED_TOPOLOGY_CONTRACT, "version": 1},
            "revision": self._revision,
            "ready": bool(roles) and self._healthy and all(bool(item["configured"]) for item in roles),
            "healthy": self._healthy,
            "roles": roles,
        }

    def redacted_document(self, required_roles: tuple[str, ...] = ()) -> dict[str, object]:
        roles = set(required_roles) | set(self._bindings)
        return self.readiness(tuple(sorted(roles)))

    async def async_replace(
        self,
        expected_revision: object,
        bindings: object,
        migration_expectations: object | None = None,
    ) -> dict[str, object]:
        self._require_loaded()
        if type(expected_revision) is not int or expected_revision < 0:
            raise ProtectedTopologyViolation("protected topology revision is invalid")
        if not isinstance(bindings, list) or len(bindings) > 128:
            raise ProtectedTopologyViolation("protected topology bindings are invalid")
        parsed = [_binding_from_payload(item) for item in bindings]
        roles = [item.role for item in parsed]
        if len(roles) != len(set(roles)):
            raise ProtectedTopologyViolation("protected topology role is duplicated")
        parsed_expectations = _migration_expectations_from_payload(migration_expectations)
        async with self._lock:
            # The value may have changed while this request waited for a
            # previous save. Re-check it while owning the same lock as writes.
            if not self._healthy:
                raise ProtectedTopologyViolation(
                    "protected topology storage is unhealthy", storage=True
                )
            if expected_revision != self._revision:
                raise ProtectedTopologyViolation("protected topology revision is stale", stale=True)
            self._validate_runtime_bindings(parsed)
            next_revision = self._revision + 1
            updated_at = _timestamp(self._now)
            stored = {
                "version": PROTECTED_TOPOLOGY_VERSION,
                "revision": next_revision,
                "updatedAt": updated_at,
                "bindings": [item.to_payload() for item in sorted(parsed, key=lambda b: b.role)],
            }
            expectation_payload = _migration_expectations_to_payload(parsed_expectations)
            if expectation_payload:
                stored["migrationExpectations"] = expectation_payload
            try:
                saved = await self._store.async_save(deepcopy(stored))
            except Exception as error:  # noqa: BLE001
                self._healthy = False
                raise ProtectedTopologyViolation(
                    "protected topology storage save failed", storage=True
                ) from error
            if saved is False:
                self._healthy = False
                raise ProtectedTopologyViolation(
                    "protected topology storage save failed", storage=True
                )
            self._revision = next_revision
            self._updated_at = updated_at
            self._bindings = {item.role: item for item in parsed}
            self._migration_expectations = parsed_expectations
            self._healthy = True
        return self.redacted_document()

    def _validate_runtime_bindings(self, bindings: list[ProtectedRoleBinding]) -> None:
        for binding in bindings:
            if binding.target_id is not None and binding.entity_id is not None:
                raise ProtectedTopologyViolation("protected topology target identity is invalid")
            if binding.target_id is not None:
                if not callable(self._catalog_resolver):
                    raise ProtectedTopologyViolation(
                        "protected topology target validation is unavailable"
                    )
                if self._resolved_target(binding.target_id) is None:
                    raise ProtectedTopologyViolation("protected topology target is unavailable")
            if binding.entity_id is not None:
                if not callable(self._entity_validator):
                    raise ProtectedTopologyViolation(
                        "protected topology entity validation is unavailable"
                    )
                if not self._validated_entity(binding.entity_id):
                    raise ProtectedTopologyViolation("protected topology entity is unavailable")

    def _catalog_device(self, target_id: str) -> object | None:
        """Call the Home Assistant catalog boundary without exposing its failures."""

        resolver = self._catalog_resolver
        if not callable(resolver):
            return None
        try:
            return resolver(target_id)
        except Exception:  # noqa: BLE001
            return None

    def _resolved_target(self, target_id: str) -> str | None:
        device = self._catalog_device(target_id)
        return target_id if getattr(device, "target_id", None) == target_id else None

    def _validated_entity(self, entity_id: str) -> bool:
        """Call the Home Assistant entity boundary without exposing its failures."""

        validator = self._entity_validator
        if not callable(validator):
            return False
        try:
            return validator(entity_id) is True
        except Exception:  # noqa: BLE001
            return False

    def _require_loaded(self) -> None:
        if not self._loaded:
            raise ProtectedTopologyStorageError("protected topology service is not loaded")

    def _clear_unhealthy_state(self) -> None:
        """Forget untrusted state after a failed read before any caller can use it."""

        self._revision = 0
        self._updated_at = ""
        self._bindings = {}
        self._migration_expectations = {
            key: () for key in _MIGRATION_EXPECTATION_KEYS
        }
        self._healthy = False


def role_targets_payload(
    resolver: Callable[[str], str | None], roles: Mapping[str, str]
) -> dict[str, object]:
    """Build a redacted control snapshot: role alias -> runtime target id.

    The caller chooses short aliases (``chandelier``/``fan``); no entity ids are
    included.  Missing roles are reported by alias and make ``ready`` false.
    """

    values: dict[str, str] = {}
    missing: list[str] = []
    for alias, role in roles.items():
        target_id = resolver(role)
        if isinstance(target_id, str) and target_id:
            values[alias] = target_id
        else:
            missing.append(alias)
    return {
        "ready": not missing,
        "roles": values,
        "missing": sorted(missing),
    }


__all__ = [name for name in globals() if name.startswith("ROLE_")] + [
    "CURTAIN_ROLES",
    "PROTECTED_TOPOLOGY_CONTRACT",
    "PROTECTED_TOPOLOGY_VERSION",
    "ProtectedTopologyService",
    "ProtectedTopologyViolation",
    "ProtectedTopologyStorageError",
    "role_targets_payload",
]
