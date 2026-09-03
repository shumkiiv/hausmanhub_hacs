"""Canonical tablet climate projection and durable typed operation service."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Protocol

from ..correlation import resolve_correlation_id, validate_correlation_id
from ..climate_revision import MAX_JS_SAFE_INTEGER, is_control_revision
from ..climate_ledger_keyring import ClimateLedgerKeyring
from ..climate_storage_errors import ClimateOperationRevisionConflict
from .contour_apply import ContourApplyStatus, ContourApplyViolation
from .contour_override import TemporaryTemperatureViolation
from .home_climate_targets import HomeClimateTargetsViolation


CLIMATE_RUNTIME_CONTRACT_NAME = "hausman-hub-climate-runtime"
CLIMATE_ACTION_CONTRACT_NAME = "hausman-hub-climate-action-request"
CLIMATE_OPERATION_CONTRACT_NAME = "hausman-hub-climate-operation"
CLIMATE_TABLET_CONTRACT_VERSION = 1
CLIMATE_RUNTIME_PATH = "/api/hausman_hub/v1/climate/runtime"
CLIMATE_ACTION_PATH = "/api/hausman_hub/v1/climate/actions"
CLIMATE_OPERATION_PATH = "/api/hausman_hub/v1/climate/operations/{operation_id}"
CLIMATE_OPERATION_TTL_MS = 60_000
MAX_CLIMATE_OPERATION_RECORDS = 256
# A home can contain up to 512 independently controlled climate devices.  A
# reliable desired intent keeps its exact originating receipt until replaced,
# so the ordinary operation ledger must hold more than the recovery-specific
# retention bound.  Recovery records and disposable preflights remain capped
# at 256.
MAX_RELIABLE_OPERATION_RECORDS = 1024
RECOVERY_PREFLIGHT_TTL_MS = 60_000

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SUPPORTED_ACTIONS = frozenset(
    {
        "set_home_targets",
        "synchronize_home",
        "set_room_target",
        "set_room_humidity_target",
        "set_room_min_target",
        "set_room_target_strategy",
        "turn_room_off",
        "clear_room_override",
        "set_room_mode",
        "set_device_mode",
    }
)
_SUPPORTED_ROOM_ACTIONS = frozenset(
    {
        "set_room_target",
        "set_room_humidity_target",
        "set_room_min_target",
        "set_room_target_strategy",
        "turn_room_off",
        "clear_room_override",
        "set_room_mode",
        "set_device_mode",
    }
)
_ROOM_BLOCK_REASON_MAP = {
    "bridge_disabled": "climate_disabled",
    "shadow_only": "shadow_only",
    "room_not_selected": "room_not_in_canary",
    "state_stale": "state_stale",
    "registry_mismatch": "registry_mismatch",
    "authority_not_ready": "authority_not_ready",
    "device_unavailable": "device_unavailable",
    "actions_unsupported": "action_unsupported",
    "evidence_not_ready": "authority_not_ready",
    "operation_pending": "operation_pending",
    "needs_reimport": "registry_mismatch",
}
_ALL_ACTIONS = frozenset(
    {
        "set_home_targets",
        "synchronize_home",
        "set_room_target",
        "clear_room_override",
        "set_room_mode",
        "set_device_mode",
        "set_room_humidity_target",
        "set_room_min_target",
        "set_room_target_strategy",
        "turn_room_off",
    }
)
_OPERATION_REASONS = frozenset(
    {
        "none",
        "climate_disabled",
        "shadow_only",
        "state_stale",
        "authority_not_ready",
        "room_not_in_canary",
        "registry_mismatch",
        "cooldown_active",
        "operation_pending",
        "action_unsupported",
        "device_unavailable",
        "read_back_mismatch",
        "confirmation_timeout",
        "internal_error",
    }
)


class ClimateTabletViolation(ValueError):
    """A public tablet request is malformed or conflicts with safe runtime state."""

    def __init__(self, message: str, *, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.code = code


class ClimateTabletUnavailable(RuntimeError):
    """The canonical tablet climate projection cannot be read safely."""


class _HeldAsyncLock:
    """No-op context used only while the service already owns its lock."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class ClimateTabletOperationNotFound(LookupError):
    """The requested bounded operation does not exist."""


class ClimateTabletOperationStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...

    async def async_load_reliable_scope_bindings(self) -> object | None: ...

    async def async_save_reliable_scope_bindings(
        self, bindings: dict[str, object]
    ) -> None: ...


class ClimateTabletRuntime(Protocol):
    configuration: object

    async def async_public_snapshot(self) -> dict[str, object]: ...

    async def async_temporary_temperature(
        self, payload: object, now: object
    ) -> object: ...

    async def async_home_climate_targets(self, payload: object) -> object: ...

    async def async_synchronize_climate(self) -> object: ...

    async def async_room_humidity_target(
        self, *, request_id: str, room_id: str, target_humidity: int
    ) -> object: ...

    async def async_room_minimum_temperature(
        self, *, request_id: str, room_id: str, minimum_temperature: float
    ) -> object: ...

    async def async_room_target_strategy(
        self, *, request_id: str, room_id: str, target_strategy: str
    ) -> object: ...

    async def async_turn_room_off(self, *, request_id: str, room_id: str) -> object: ...

    async def async_recover_device(
        self, *, request_id: str, room_id: str, device_id: str,
        desired: Mapping[str, object], expected_control_revision: int | None = None,
    ) -> object: ...

    async def async_recover_offline_device(
        self, *, room_id: str, device_id: str, expected_control_revision: int
    ) -> object: ...

    async def async_set_room_mode(self, room_id: object, mode: object) -> object: ...

    async def async_set_device_mode(
        self, room_id: object, device_id: object, mode: object
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class ClimateTabletActionRequest:
    request_id: str
    correlation_id: str
    expected_state_revision: int
    action: str
    room_id: str | None
    parameters: dict[str, object]
    expected_control_revision: int | None = None
    reliability_profile: str | None = None

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            {
                "expected_state_revision": self.expected_state_revision,
                # Correlation is part of the externally observable command
                # identity.  Reusing an id with another correlation must not
                # replay a receipt which belongs to a different trace.
                "correlation_id": self.correlation_id,
                "action": self.action,
                "room_id": self.room_id,
                "parameters": self.parameters,
                "expected_control_revision": self.expected_control_revision,
                "reliability_profile": self.reliability_profile,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class _StoredOperation:
    fingerprint: str
    request: ClimateTabletActionRequest
    receipt: dict[str, object]
    dispatch_ledger: dict[str, object] | None = None


def parse_climate_tablet_action(payload: object) -> ClimateTabletActionRequest:
    """Accept only the strict public action envelope and bounded parameters."""

    if not isinstance(payload, Mapping) or any(
        not isinstance(key, str) for key in payload
    ):
        raise ClimateTabletViolation("climate action must be an object")
    required_fields = {
        "contract",
        "request_id",
        "expected_state_revision",
        "action",
        "room_id",
        "parameters",
    }
    optional_fields = {"correlation_id", "expected_control_revision", "reliability_profile"}
    if not required_fields <= set(payload) <= required_fields | optional_fields:
        raise ClimateTabletViolation("climate action fields are invalid")
    contract = payload.get("contract")
    if contract != {
        "name": CLIMATE_ACTION_CONTRACT_NAME,
        "version": CLIMATE_TABLET_CONTRACT_VERSION,
    }:
        raise ClimateTabletViolation("climate action contract is invalid")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise ClimateTabletViolation("climate action request id is invalid")
    try:
        correlation_id = resolve_correlation_id(
            payload,
            field="correlation_id",
            fallback=request_id,
        )
    except ValueError as error:
        raise ClimateTabletViolation("climate action correlation id is invalid") from error
    revision = payload.get("expected_state_revision")
    if not is_control_revision(revision):
        raise ClimateTabletViolation("climate action revision is invalid")
    reliability_profile = payload.get("reliability_profile")
    expected_control_revision = payload.get("expected_control_revision")
    if reliability_profile is not None:
        if (
            reliability_profile != "climate_reliability_v1"
            or not is_control_revision(expected_control_revision)
        ):
            raise ClimateTabletViolation("climate reliability profile is invalid")
    elif expected_control_revision is not None:
        raise ClimateTabletViolation("climate control revision requires reliability profile")
    action = payload.get("action")
    if not isinstance(action, str) or action not in _ALL_ACTIONS:
        raise ClimateTabletViolation("climate action is unsupported")
    room_id = payload.get("room_id")
    if room_id is not None and (
        not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None
    ):
        raise ClimateTabletViolation("climate action room is invalid")
    parameters = payload.get("parameters")
    if not isinstance(parameters, Mapping) or any(
        not isinstance(key, str) for key in parameters
    ):
        raise ClimateTabletViolation("climate action parameters are invalid")
    normalized = dict(parameters)
    if action == "set_home_targets":
        if room_id is not None or not 1 <= len(normalized) <= 2:
            raise ClimateTabletViolation("home climate target is invalid")
        if not set(normalized) <= {"target_temperature", "target_humidity"}:
            raise ClimateTabletViolation("home climate target fields are invalid")
        if "target_temperature" in normalized:
            _validate_temperature(normalized["target_temperature"])
        if "target_humidity" in normalized:
            _validate_humidity(normalized["target_humidity"])
    elif action == "synchronize_home":
        if room_id is not None or normalized:
            raise ClimateTabletViolation("home climate synchronization is invalid")
    elif action == "set_room_target":
        _require_room(room_id)
        if set(normalized) != {"target_temperature"}:
            raise ClimateTabletViolation("room climate target fields are invalid")
        _validate_temperature(normalized.get("target_temperature"))
    elif action in {"clear_room_override", "turn_room_off"}:
        _require_room(room_id)
        if normalized:
            raise ClimateTabletViolation("climate action parameters must be empty")
    elif action == "set_room_mode":
        _require_room(room_id)
        if set(normalized) != {"mode"} or normalized.get("mode") not in {
            "automatic",
            "manual",
        }:
            raise ClimateTabletViolation("room climate mode is invalid")
    elif action == "set_device_mode":
        _require_room(room_id)
        if (
            set(normalized) != {"device_id", "mode"}
            or not isinstance(normalized.get("device_id"), str)
            or _STABLE_ID.fullmatch(normalized["device_id"]) is None
            or normalized.get("mode") not in {"automatic", "manual"}
        ):
            raise ClimateTabletViolation("device climate mode is invalid")
    elif action == "set_room_humidity_target":
        _require_room(room_id)
        if set(normalized) != {"target_humidity"}:
            raise ClimateTabletViolation("room humidity target fields are invalid")
        _validate_humidity(normalized.get("target_humidity"))
    elif action == "set_room_min_target":
        _require_room(room_id)
        if set(normalized) != {"minimum_temperature"}:
            raise ClimateTabletViolation("room minimum target fields are invalid")
        _validate_temperature(normalized.get("minimum_temperature"))
    elif action == "set_room_target_strategy":
        _require_room(room_id)
        if set(normalized) != {"target_strategy"} or normalized.get(
            "target_strategy"
        ) not in {"soft", "normal", "aggressive"}:
            raise ClimateTabletViolation("room target strategy is invalid")
    return ClimateTabletActionRequest(
        request_id=request_id,
        correlation_id=correlation_id,
        expected_state_revision=revision,
        action=action,
        room_id=room_id,
        parameters=normalized,
        expected_control_revision=expected_control_revision,
        reliability_profile=reliability_profile,
    )


def climate_tablet_snapshot(
    home: object | None,
    *,
    climate_mode: str,
    active_operations: tuple[dict[str, object], ...] = (),
    confirmed_operations: tuple[dict[str, object], ...] = (),
    generated_at: int | None = None,
) -> dict[str, object]:
    """Project the strict runtime contract from the existing native home model."""

    if climate_mode == "disabled":
        return _disabled_snapshot(generated_at, active_operations)
    if climate_mode not in {"shadow", "managed"} or not isinstance(home, Mapping):
        raise ClimateTabletUnavailable("climate runtime is unavailable")
    shadow = climate_mode == "shadow"
    climate = home.get("climate")
    rooms_value = home.get("rooms")
    contours_value = home.get("contours")
    reconciliation = home.get("reconciliation")
    revision = home.get("state_revision")
    observed_at = home.get("generated_at")
    if (
        not isinstance(climate, Mapping)
        or not isinstance(rooms_value, list)
        or not isinstance(contours_value, list)
        or not isinstance(reconciliation, Mapping)
        or not is_control_revision(revision)
        or type(observed_at) is not int
    ):
        raise ClimateTabletUnavailable("climate home projection is invalid")
    fresh = climate.get("fresh") is True
    reconciliation_matches = reconciliation.get("matches") is True
    contour = next(
        (
            item
            for item in contours_value
            if isinstance(item, Mapping) and item.get("kind") == "climate"
        ),
        None,
    )
    contour_rooms = {
        room.get("id"): room
        for room in (
            contour.get("rooms", []) if isinstance(contour, Mapping) else []
        )
        if isinstance(room, Mapping) and isinstance(room.get("id"), str)
    }
    pending_rooms = {
        operation.get("room_id")
        for operation in active_operations
        if operation.get("status") == "pending"
    }
    confirmed_by_room: dict[str | None, dict[str, object]] = {}
    for operation in sorted(
        confirmed_operations,
        key=lambda item: item.get("updated_at", 0),
    ):
        room_id = operation.get("room_id")
        if room_id is None or isinstance(room_id, str):
            confirmed_by_room[room_id] = operation
    scopes = {
        device.get("control_scope")
        for room in rooms_value
        if isinstance(room, Mapping)
        for device in room.get("devices", [])
        if isinstance(device, Mapping)
    }
    phase = "shadow" if shadow else (
        "managed" if "managed" in scopes else (
            "canary" if "canary" in scopes else "ready_for_canary"
        )
    )
    projected_rooms: list[dict[str, object]] = []
    any_room_enabled = False
    for room in rooms_value:
        if not isinstance(room, Mapping):
            raise ClimateTabletUnavailable("climate room projection is invalid")
        room_id = room.get("id")
        if not isinstance(room_id, str):
            raise ClimateTabletUnavailable("climate room id is invalid")
        contour_room = contour_rooms.get(room_id)
        temporary = (
            contour_room.get("temporary_temperature")
            if isinstance(contour_room, Mapping)
            else None
        )
        room_reasons: list[str] = []
        if shadow:
            room_reasons.append("shadow_only")
        if not fresh:
            room_reasons.append("state_stale")
        if not reconciliation_matches:
            room_reasons.append("registry_mismatch")
        if room_id in pending_rooms:
            room_reasons.append("operation_pending")
        native_control = room.get("control")
        native_allowed = (
            native_control.get("allowed_actions")
            if isinstance(native_control, Mapping)
            else None
        )
        native_reasons = (
            native_control.get("blocked_reasons")
            if isinstance(native_control, Mapping)
            else None
        )
        native_enabled = (
            isinstance(native_control, Mapping)
            and native_control.get("enabled") is True
        )
        allowed_actions: list[str] = []
        if not shadow and not room_reasons and native_enabled:
            allowed_actions = [
                action
                for action in (native_allowed if isinstance(native_allowed, list) else [])
                if isinstance(action, str)
                and action in _SUPPORTED_ROOM_ACTIONS
            ]
        # Manual exclusions only change restart-safe HausmanHub ownership.
        # They do not call a physical device service, so they remain available
        # when freshness or readiness blocks active climate control.  This is
        # also the recovery path for an unavailable contour device.
        if (
            not shadow
            and room_id not in pending_rooms
            and isinstance(native_allowed, list)
            and "set_room_mode" in native_allowed
            and "set_room_mode" not in allowed_actions
        ):
            allowed_actions.append("set_room_mode")
        if (
            isinstance(temporary, Mapping)
            and temporary.get("active") is True
            and "set_room_target" in allowed_actions
            and "clear_room_override" not in allowed_actions
        ):
            # Returning to the saved schedule is the inverse of a supported
            # temporary target. It is a durable contour change and is routed
            # through the same guarded native control boundary.
            allowed_actions.append("clear_room_override")
        if allowed_actions:
            room_reasons = []
        if not room_reasons and not allowed_actions and isinstance(native_reasons, list):
            room_reasons.extend(
                mapped
                for reason in native_reasons
                if isinstance(reason, str)
                and (mapped := _ROOM_BLOCK_REASON_MAP.get(reason)) is not None
                and mapped not in room_reasons
            )
        if not allowed_actions and not room_reasons:
            room_reasons.append("action_unsupported")
        enabled = bool(allowed_actions)
        any_room_enabled = any_room_enabled or enabled
        devices = room.get("devices")
        if not isinstance(devices, list):
            raise ClimateTabletUnavailable("climate room devices are invalid")
        active_target = room.get("active_target")
        saved_profiles = room.get("saved_profiles")
        active_profile = (
            saved_profiles.get("active") if isinstance(saved_profiles, Mapping) else None
        )
        active_settings = (
            saved_profiles.get(active_profile)
            if isinstance(saved_profiles, Mapping) and isinstance(active_profile, str)
            else None
        )
        action_inputs = (
            room.get("control", {}).get("action_inputs", {})
            if isinstance(room.get("control"), Mapping)
            else {}
        )
        room_target_input = (
            action_inputs.get("set_room_target", {}).get("target_temperature", {})
            if isinstance(action_inputs, Mapping)
            else {}
        )
        temperature_range = _public_range(
            room_target_input,
            minimum=18,
            maximum=28,
            step=0.5,
        )
        humidity_input = (
            action_inputs.get("set_room_humidity_target", {}).get(
                "target_humidity", {}
            )
            if isinstance(action_inputs, Mapping)
            else {}
        )
        last_confirmed = confirmed_by_room.get(room_id) or confirmed_by_room.get(None)
        projected_rooms.append(
            {
                "id": room_id,
                "name": room.get("name"),
                "temperature": room.get("temperature"),
                "humidity": room.get("humidity"),
                "target_temperature": room.get("target_temperature"),
                "target_humidity": room.get("target_humidity"),
                "minimum_temperature": temperature_range["minimum"],
                "temperature_range": temperature_range,
                "humidity_range": _public_range(
                    humidity_input,
                    minimum=30,
                    maximum=70,
                    step=1,
                ),
                "mode": room.get("mode") if room.get("mode") in {
                    "automatic", "manual", "off", "unknown"
                } else "unknown",
                "target_strategy": (
                    active_target.get("strategy")
                    if isinstance(active_target, Mapping)
                    else (
                        active_settings.get("strategy")
                        if isinstance(active_settings, Mapping)
                        else "unknown"
                    )
                ),
                "active_profile": (
                    active_profile if active_profile in {"day", "night"} else None
                ),
                "temporary_override": {
                    "active": (
                        isinstance(temporary, Mapping)
                        and temporary.get("active") is True
                    ),
                    "target_temperature": (
                        temporary.get("temperature")
                        if isinstance(temporary, Mapping)
                        and temporary.get("active") is True
                        else None
                    ),
                    "ends_at": (
                        temporary.get("ends_at")
                        if isinstance(temporary, Mapping)
                        and temporary.get("active") is True
                        and isinstance(temporary.get("ends_at"), str)
                        else None
                    ),
                },
                "authority": (
                    "legacy_climate_core" if shadow else "hausman_hub"
                ),
                "control": {
                    "enabled": enabled,
                    "allowed_actions": allowed_actions,
                    "blocked_reasons": room_reasons,
                },
                "devices": [
                    _project_device(device, last_confirmed) for device in devices
                ],
            }
        )
    home_base_allowed = bool(
        not shadow
        and fresh
        and reconciliation_matches
        and phase == "managed"
        and isinstance(contour, Mapping)
        and contour.get("mode") == "automatic"
        and not active_operations
    )
    # Whole-home targets are dispatched through the typed native climate
    # operation, not the legacy settings-apply endpoint.  Its preflight is
    # represented by the same current room controls used by that operation.
    # Do not let an unrelated legacy capability hide a valid typed command.
    home_targets_allowed = bool(
        home_base_allowed
        and projected_rooms
        and all(
            room["mode"] == "automatic"
            and "set_room_target" in room["control"]["allowed_actions"]
            for room in projected_rooms
        )
    )
    home_actions = [
        *(["set_home_targets"] if home_targets_allowed else []),
        *(["synchronize_home"] if home_base_allowed else []),
    ]
    home_allowed = bool(home_actions)
    if home_allowed:
        home_reasons = []
    elif shadow:
        home_reasons = ["shadow_only"]
        if not fresh:
            home_reasons.append("state_stale")
        if not reconciliation_matches:
            home_reasons.append("registry_mismatch")
        if active_operations:
            home_reasons.append("operation_pending")
    else:
        home_reasons = _aggregate_block_reasons(
            fresh=fresh,
            reconciliation_matches=reconciliation_matches,
            pending=bool(active_operations),
        )
    commands_enabled = any_room_enabled or home_allowed
    blocked_reasons = [] if commands_enabled else list(home_reasons)
    if not blocked_reasons and not commands_enabled:
        blocked_reasons.append("action_unsupported")
    return {
        "contract": {
            "name": CLIMATE_RUNTIME_CONTRACT_NAME,
            "version": CLIMATE_TABLET_CONTRACT_VERSION,
        },
        "generated_at": observed_at,
        "state_revision": revision,
        "control_revision": revision,
        "phase": phase,
        "authority": "legacy_climate_core" if shadow else "hausman_hub",
        "fresh": fresh,
        "commands_enabled": commands_enabled,
        "blocked_reasons": blocked_reasons,
        "home_control": {
            "enabled": home_allowed,
            "allowed_actions": home_actions,
            "blocked_reasons": home_reasons,
        },
        "rooms": projected_rooms,
        "active_operations": [dict(item) for item in active_operations],
    }


class ClimateTabletService:
    """Persist request identity before delegating one typed action to runtime."""

    def __init__(
        self,
        runtime: ClimateTabletRuntime,
        store: ClimateTabletOperationStore,
        *,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
        local_now: Callable[[], object] | None = None,
    ) -> None:
        self._runtime = runtime
        self._store = store
        scope_key = getattr(store, "reliable_scope_integrity_key", None)
        self._reliable_scope_integrity_key = (
            scope_key if isinstance(scope_key, ClimateLedgerKeyring) else
            scope_key.encode("ascii") if isinstance(scope_key, str)
            and re.fullmatch(r"[a-f0-9]{64}", scope_key) else None
        )
        self._external_ledger_keyring = (
            scope_key if isinstance(scope_key, ClimateLedgerKeyring)
            and scope_key.source_path is not None else None
        )
        self._operation_id_factory = operation_id_factory or (
            lambda: secrets.token_hex(16)
        )
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._local_now = local_now or (lambda: datetime.now().astimezone())
        self._records_by_request: dict[str, _StoredOperation] = {}
        self._request_by_operation: dict[str, str] = {}
        # A recovery record deliberately keeps the receipt and the private
        # per-device ledger together.  The receipt is a projection, never the
        # source of truth for whether a physical call may be repeated.
        self._recoveries_by_request: dict[str, dict[str, object]] = {}
        self._recovery_by_operation: dict[str, str] = {}
        self._recovery_preflights: dict[str, dict[str, object]] = {}
        self._control_revision = 0
        self._desired_intents: dict[str, dict[str, object]] = {}
        self._reliable_scope_bindings: dict[str, dict[str, object]] = {}
        # Legacy HTTP replies keep their execution facts outside the typed
        # receipt.  The sidecar is private and never changes that contract.
        self._legacy_home_execution_facts: dict[str, dict[str, object]] = {}
        # Bindings must outlive a failed main-ledger write.  They are removed
        # only after the corresponding main record is durably gone.
        self._reliable_scope_binding_cleanup: set[str] = set()
        self._persistence_failed = False
        self._initialized = False
        self._last_reliability_metadata: dict[tuple[str, str], dict[str, object]] = {}
        self._last_safe_snapshot: dict[str, object] | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Restore only exact bounded operation records; damaged data fails closed."""

        payload = await self._store.async_load()
        if payload is None:
            self._initialized = True
            return
        if not isinstance(payload, Mapping) or set(payload) not in (
            {"version", "records"}, {"version", "records", "recoveries"},
            {"version", "records", "recoveries", "control_revision"},
            {"version", "records", "recoveries", "control_revision", "desired_intents"},
            {"version", "records", "recoveries", "control_revision", "desired_intents", "recovery_preflights"},
            {"version", "records", "recoveries", "control_revision", "desired_intents", "direct_control_records"},
            {"version", "records", "recoveries", "control_revision", "desired_intents", "direct_control_records", "recovery_preflights"},
        ):
            raise ClimateTabletUnavailable("stored climate operations are invalid")
        if payload.get("version") not in {1, 2, 3, 4, 5, 6} or not isinstance(payload.get("records"), list):
            raise ClimateTabletUnavailable("stored climate operation version is invalid")
        stored_revision = payload.get("control_revision", 0)
        if not is_control_revision(stored_revision):
            raise ClimateTabletUnavailable("stored climate control revision is invalid")
        load_bindings = getattr(self._store, "async_load_reliable_scope_bindings", None)
        if not callable(load_bindings):
            raise ClimateTabletUnavailable("reliable climate scope storage is unavailable")
        try:
            stored_bindings = await load_bindings()
        except Exception as error:
            raise ClimateTabletUnavailable("reliable climate scope storage is unavailable") from error
        if not isinstance(stored_bindings, Mapping) or len(stored_bindings) > MAX_RELIABLE_OPERATION_RECORDS + 3:
            raise ClimateTabletUnavailable("reliable climate scope storage is invalid")
        stored_bindings = dict(stored_bindings)
        state_checkpoint = stored_bindings.pop("__tablet_state__", None)
        legacy_execution_facts = stored_bindings.pop("__legacy_home_execution_facts__", {})
        if (
            not isinstance(legacy_execution_facts, Mapping)
            or len(legacy_execution_facts) > MAX_RELIABLE_OPERATION_RECORDS
            or any(
                not isinstance(request_id, str)
                or _REQUEST_ID.fullmatch(request_id) is None
                or not _valid_legacy_home_execution_fact(fact)
                for request_id, fact in legacy_execution_facts.items()
            )
        ):
            raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
        if payload.get("version") == 6 and not _valid_tablet_state_checkpoint(
            state_checkpoint, payload, self._reliable_scope_integrity_key
        ):
            raise ClimateTabletUnavailable("stored climate tablet state is invalid")
        scope_bindings: dict[str, dict[str, object]] = {}
        for request_id, binding in stored_bindings.items():
            if not isinstance(request_id, str) or not isinstance(binding, Mapping):
                raise ClimateTabletUnavailable("reliable climate scope storage is invalid")
            scope_bindings[request_id] = dict(binding)
        minimum_control_revision = 0
        requires_save = False
        records: dict[str, _StoredOperation] = {}
        operations: dict[str, str] = {}
        for item in payload["records"]:
            keys = set(item) if isinstance(item, Mapping) else set()
            if keys not in (
                {"request_id", "fingerprint", "request", "receipt"},
                {"request_id", "fingerprint", "request", "receipt", "dispatch_ledger"},
            ):
                raise ClimateTabletUnavailable("stored climate operation is invalid")
            request_id = item.get("request_id")
            fingerprint = item.get("fingerprint")
            request_payload = item.get("request")
            receipt = item.get("receipt")
            dispatch_ledger = item.get("dispatch_ledger")
            if (
                not isinstance(request_id, str)
                or _REQUEST_ID.fullmatch(request_id) is None
                or not isinstance(fingerprint, str)
                or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
                or not isinstance(receipt, Mapping)
            ):
                raise ClimateTabletUnavailable("stored climate operation fields are invalid")
            try:
                request = parse_climate_tablet_action(request_payload)
            except ClimateTabletViolation as error:
                raise ClimateTabletUnavailable(
                    "stored climate operation request is invalid"
                ) from error
            if request.request_id != request_id:
                raise ClimateTabletUnavailable("stored climate operation request is invalid")
            if request.fingerprint != fingerprint:
                if not _is_legacy_tablet_fingerprint(
                    payload.get("version"), request, fingerprint
                ):
                    raise ClimateTabletUnavailable("stored climate operation request is invalid")
                # Versions 1-5 predate correlation and reliable-operation
                # fields in this identity.  Accept only their exact canonical
                # digest, then persist the current digest before serving a
                # duplicate.  Anything else remains fail-closed.
                fingerprint = request.fingerprint
                requires_save = True
            normalized = _validate_receipt(dict(receipt))
            if not _receipt_matches_request(normalized, request):
                raise ClimateTabletUnavailable("stored climate operation receipt is invalid")
            if request.reliability_profile == "climate_reliability_v1":
                if not _valid_reliable_scope_binding(
                    scope_bindings.get(request_id), request, normalized,
                    dispatch_ledger, self._reliable_scope_integrity_key,
                ):
                    raise ClimateTabletUnavailable("stored climate operation scope is invalid")
                if dispatch_ledger is None:
                    # Reliable records are introduced together with the durable
                    # dispatch ledger.  A ledgerless record has no honest
                    # physical boundary, regardless of a forged outer version.
                    raise ClimateTabletUnavailable("stored climate operation ledger is invalid")
                elif not _valid_reliable_dispatch_ledger(
                    dispatch_ledger, normalized, request
                ):
                    raise ClimateTabletUnavailable("stored climate operation ledger is invalid")
                if isinstance(dispatch_ledger, Mapping) and dispatch_ledger.get("state") == "started":
                    baseline = _metadata_from_reliable_dispatch_ledger(dispatch_ledger)
                    normalized = _ambiguous_started_reliable_receipt(
                        normalized, request, self._safe_now()
                    )
                    dispatch_ledger = _reliable_dispatch_ledger(
                        normalized, "terminal_mixed",
                        dispatched_at=dispatch_ledger.get("dispatched_at"),
                        metadata=baseline,
                    )
                    if not _valid_reliable_dispatch_ledger(
                        dispatch_ledger, normalized, request
                    ):
                        raise ClimateTabletUnavailable("stored climate operation ledger is invalid")
                    requires_save = True
            elif dispatch_ledger is not None:
                raise ClimateTabletUnavailable("stored climate operation ledger is invalid")
            operation_id = normalized["operation_id"]
            if request_id in records or operation_id in operations:
                raise ClimateTabletUnavailable("stored climate operation is duplicated")
            records[request_id] = _StoredOperation(
                fingerprint, request, normalized,
                dict(dispatch_ledger) if isinstance(dispatch_ledger, Mapping) else None,
            )
            operations[operation_id] = request_id
            resulting = normalized.get("resulting_control_revision")
            if type(resulting) is int:
                minimum_control_revision = max(minimum_control_revision, resulting)
        if len(records) > MAX_RELIABLE_OPERATION_RECORDS:
            raise ClimateTabletUnavailable("stored climate operation history is too large")
        self._records_by_request = records
        self._request_by_operation = operations
        self._reliable_scope_bindings = {
            request_id: scope_bindings[request_id]
            for request_id, record in records.items()
            if record.request.reliability_profile == "climate_reliability_v1"
        }
        if isinstance(state_checkpoint, Mapping):
            self._reliable_scope_bindings["__tablet_state__"] = dict(state_checkpoint)
        self._legacy_home_execution_facts = {}
        for correlation_id, fact in legacy_execution_facts.items():
            record = records.get(fact["request_id"])
            if not _legacy_home_execution_fact_matches_record(correlation_id, fact, record):
                raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
            self._legacy_home_execution_facts[correlation_id] = dict(fact)
        stored_intents = payload.get("desired_intents", {})
        if not isinstance(stored_intents, Mapping) or len(stored_intents) > 4096:
            raise ClimateTabletUnavailable("stored climate desired intent is invalid")
        self._desired_intents = {}
        for key, value in stored_intents.items():
            if not isinstance(key, str) or not isinstance(value, Mapping):
                raise ClimateTabletUnavailable("stored climate desired intent is invalid")
            intent = _validate_desired_intent(key, value)
            origin = records.get(intent["origin_request_id"])
            if (
                origin is None
                or origin.fingerprint != intent["request_fingerprint"]
                or _intent_key(origin.request) != key
                or origin.request.action != intent["action"]
                or origin.request.room_id != intent["room_id"]
                or (
                    origin.request.reliability_profile == "climate_reliability_v1"
                    and origin.receipt.get("resulting_control_revision")
                    != intent["control_revision"]
                )
            ):
                raise ClimateTabletUnavailable("stored climate desired intent is invalid")
            self._desired_intents[key] = intent
            minimum_control_revision = max(
                minimum_control_revision, intent["control_revision"]
            )
        if len(self._desired_intents) != len(stored_intents):
            raise ClimateTabletUnavailable("stored climate desired intent is invalid")
        recoveries = payload.get("recoveries", [])
        if not isinstance(recoveries, list) or len(recoveries) > MAX_CLIMATE_OPERATION_RECORDS:
            raise ClimateTabletUnavailable("stored climate recovery is invalid")
        for stored in recoveries:
            if not isinstance(stored, Mapping):
                raise ClimateTabletUnavailable("stored climate recovery is invalid")
            if set(stored) == {"receipt", "ledger", "preflight"}:
                item = dict(stored["receipt"]) if isinstance(stored.get("receipt"), Mapping) else None
                ledger = stored.get("ledger")
                preflight = stored.get("preflight")
            else:
                item = None
                ledger = None
                preflight = None
            if item is None or not isinstance(preflight, Mapping):
                raise ClimateTabletUnavailable("stored climate recovery is invalid")
            request_id = item.get("request_id")
            operation_id = item.get("operation_id")
            if (not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
                    or not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None
                    or request_id in self._recoveries_by_request or operation_id in self._recovery_by_operation):
                raise ClimateTabletUnavailable("stored climate recovery is invalid")
            if not _valid_recovery_ledger(ledger, item):
                raise ClimateTabletUnavailable("stored climate recovery ledger is invalid")
            # Old aggregate receipts did not persist a dispatch timestamp or
            # device observation evidence.  They must never be promoted by a
            # later read-back: close them now as unknown and non-replayable.
            if _recovery_ledger_lacks_dispatch_evidence(ledger):
                ledger = {
                    device_id: _recovery_leaf("started")
                    if isinstance(leaf, Mapping) and leaf.get("ledger_state", leaf.get("execution_state")) in {"started", "accepted_unverified"}
                    else dict(leaf) if isinstance(leaf, Mapping) else leaf
                    for device_id, leaf in ledger.items()
                }
                item = _freeze_unproven_recovery_receipt(item, ledger)
            if not _valid_recovery_record(item, ledger, preflight):
                raise ClimateTabletUnavailable("stored climate recovery is invalid")
            self._recoveries_by_request[request_id] = {
                "receipt": item, "ledger": dict(ledger), "preflight": dict(preflight),
            }
            self._recovery_by_operation[operation_id] = request_id
            minimum_control_revision = max(
                minimum_control_revision, item["resulting_control_revision"]
            )
        preflights = payload.get("recovery_preflights", [])
        if not isinstance(preflights, list) or len(preflights) > MAX_CLIMATE_OPERATION_RECORDS:
            raise ClimateTabletUnavailable("stored climate recovery preflight is invalid")
        now = self._safe_now()
        for item in preflights:
            if not _valid_recovery_preflight_record(item):
                raise ClimateTabletUnavailable("stored climate recovery preflight is invalid")
            if item["expires_at"] > now:
                self._recovery_preflights[item["token"]] = {"preflight": dict(item["preflight"]), "expires_at": item["expires_at"]}
        if stored_revision < minimum_control_revision:
            raise ClimateTabletUnavailable("stored climate control revision is stale")
        self._control_revision = stored_revision
        if requires_save:
            await self._async_save()
        self._initialized = True

    @property
    def reliability_ready(self) -> bool:
        """Whether this service may advertise the reliable dispatch branch.

        Read-only climate snapshots remain available without this proof.  The
        capability is deliberately narrower: setup must have initialized an
        external persistent authenticated ledger, this service must have
        loaded successfully, and no persistence operation may have failed.
        """

        return (
            self._initialized
            and not self._persistence_failed
            and self._external_ledger_keyring is not None
            and getattr(self._store, "authenticated_external_ledger_ready", False)
            is True
        )

    def _require_reliability_health(self) -> None:
        """Close the external-ledger dispatch branch after any write failure."""

        # Framework-free legacy fixtures have no external keyring and cannot
        # represent this production-only boundary. A configured external
        # keyring, however, must be healthy immediately before dispatch.
        if self._external_ledger_keyring is not None and not self.reliability_ready:
            raise ClimateTabletUnavailable("climate reliable ledger is unavailable")

    def _maintenance_unhealthy(self) -> bool:
        return self._persistence_failed or (
            self._external_ledger_keyring is not None and not self.reliability_ready
        )

    def _remember_safe_snapshot(self, payload: dict[str, object]) -> dict[str, object]:
        copied = json.loads(json.dumps(payload))
        self._last_safe_snapshot = copied
        return json.loads(json.dumps(copied))

    async def async_snapshot(self) -> dict[str, object]:
        """Read the canonical runtime projection without changing climate state."""

        async with self._lock:
            if self._maintenance_unhealthy():
                if self._last_safe_snapshot is None:
                    raise ClimateTabletUnavailable("climate runtime has no safe snapshot")
                return json.loads(json.dumps(self._last_safe_snapshot))
            await self._sync_control_revision_unlocked()
            await self._expire_pending_unlocked()
            await self._refresh_pending_unlocked()
            mode = _climate_mode(self._runtime)
            active = tuple(
                _operation_summary(record.receipt)
                for record in self._records_by_request.values()
                if record.receipt.get("final") is False
            )
            confirmed = tuple(
                _operation_summary(record.receipt)
                for record in self._records_by_request.values()
                if record.receipt.get("status") == "confirmed"
            )
            if mode == "disabled":
                projection = climate_tablet_snapshot(
                    None,
                    climate_mode=mode,
                    active_operations=active,
                    confirmed_operations=confirmed,
                    generated_at=self._safe_now(),
                )
                projection["control_revision"] = self._control_revision
                return self._remember_safe_snapshot(projection)
            try:
                home = await self._runtime.async_public_snapshot()
            except Exception as error:
                raise ClimateTabletUnavailable("climate runtime is unavailable") from error
            metadata = _reliability_metadata(home)
            private_metadata = getattr(
                self._runtime, "async_recovery_private_metadata", None
            )
            if callable(private_metadata):
                try:
                    supplied = await private_metadata()
                except Exception as error:
                    raise ClimateTabletUnavailable(
                        "climate recovery proof is unavailable"
                    ) from error
                if not isinstance(supplied, Mapping):
                    raise ClimateTabletUnavailable("climate recovery proof is invalid")
                for key, value in supplied.items():
                    if (
                        not isinstance(key, tuple)
                        or len(key) != 2
                        or not all(isinstance(item, str) for item in key)
                        or not isinstance(value, Mapping)
                    ):
                        raise ClimateTabletUnavailable("climate recovery proof is invalid")
                    metadata[key] = {
                        **metadata.get(key, {}),
                        **_validate_private_recovery_metadata(value),
                    }
            projection = climate_tablet_snapshot(
                home,
                climate_mode=mode,
                active_operations=active,
                confirmed_operations=confirmed,
            )
            projection["control_revision"] = self._control_revision
            self._last_reliability_metadata = metadata
            return self._remember_safe_snapshot(_with_reliability_projection(
                projection, self._desired_intents, self._control_revision,
                self._last_reliability_metadata,
            ))

    async def async_execute(
        self,
        payload: object,
        *,
        _lock_held: bool = False,
        _legacy_fail_fast: bool = False,
        _legacy_correlation_id: str | None = None,
    ) -> dict[str, object]:
        """Reserve, execute at most once, persist and return one operation receipt."""

        request = parse_climate_tablet_action(payload)
        # The legacy adapter has already acquired this same service lock.  Do
        # not release and re-enter it between its duplicate/readiness checks
        # and the shared reservation path.
        async with (self._lock if not _lock_held else _HeldAsyncLock()):
            if request.reliability_profile is not None:
                self._require_reliability_health()
            if self._persistence_failed:
                raise ClimateTabletUnavailable("climate persistence requires restart")
            await self._sync_control_revision_unlocked()
            await self._expire_pending_unlocked()
            await self._refresh_pending_unlocked()
            prior = self._records_by_request.get(request.request_id)
            if prior is not None:
                if prior.fingerprint != request.fingerprint:
                    raise ClimateTabletViolation(
                        "request id was already used for another climate action",
                        code="revision_conflict",
                    )
                return {**prior.receipt, "duplicate": True}
            # Legacy operations do not reserve through the shared coordinator,
            # but their receipt still advances the public control revision.
            # Refuse exhaustion before pruning, intent replacement, receipt
            # creation or any persistence attempt.
            if (
                request.reliability_profile is None
                and (
                    not is_control_revision(self._control_revision)
                    or self._control_revision >= MAX_JS_SAFE_INTEGER
                )
            ):
                raise ClimateTabletViolation(
                    "climate control revision is exhausted",
                    code="revision_conflict",
                )
            if len(self._records_by_request) >= MAX_RELIABLE_OPERATION_RECORDS:
                self._prune_oldest_final()
            if len(self._records_by_request) >= MAX_RELIABLE_OPERATION_RECORDS:
                raise ClimateTabletUnavailable("climate operation history is full")
            snapshot = await self._snapshot_unlocked()
            if _legacy_fail_fast:
                # A compatibility call has no desired-intent retry semantics:
                # readiness and pending gates must reject it before it can
                # reserve a revision, supersede another request or persist.
                _require_action_allowed(snapshot, request)
            # A disabled runtime has no authoritative inventory and cannot
            # safely retain a desired physical command.  Fail before any
            # revision reservation for both legacy and negotiated actions.
            if snapshot.get("phase") == "disabled":
                _require_action_allowed(snapshot, request)
            preflight_scope: dict[str, object] | None = None
            if (
                request.reliability_profile == "climate_reliability_v1"
                and request.action == "set_home_targets"
            ):
                preflight = getattr(
                    self._runtime, "async_preflight_home_climate_targets", None
                )
                if callable(preflight):
                    try:
                        preflight_result = await preflight({
                            "request_id": request.request_id,
                            "correlation_id": request.correlation_id,
                            "contour_id": "climate",
                            "target_temperature": request.parameters.get("target_temperature"),
                            "target_humidity": request.parameters.get("target_humidity"),
                            "confirm": True,
                        })
                        candidate_scope = (
                            preflight_result.get("resolved_scope")
                            if isinstance(preflight_result, Mapping)
                            else None
                        )
                        if not isinstance(candidate_scope, Mapping) or not _valid_frozen_scope(
                            candidate_scope, request
                        ):
                            raise ClimateTabletUnavailable(
                                "home climate target scope is unavailable"
                            )
                        preflight_scope = json.loads(json.dumps(candidate_scope))
                    except Exception as error:
                        raise ClimateTabletUnavailable(
                            "home climate target scope is unavailable"
                        ) from error
            # A desired intent may survive a temporary authority gate, but it
            # must never be created for an invented room or device.  This is
            # immutable scope validation, not a physical-permission check.
            _require_existing_action_scope(snapshot, request)
            # Enhanced actions use the durable control revision.  Reported
            # state may change between a screen refresh and a command and is
            # deliberately not an optimistic-lock token for that protocol.
            if (request.reliability_profile is None
                    and snapshot["state_revision"] != request.expected_state_revision):
                raise ClimateTabletViolation(
                    "climate state revision changed",
                    code="revision_conflict",
                )
            if (request.reliability_profile is not None
                    and snapshot.get("control_revision") != request.expected_control_revision):
                raise ClimateTabletViolation("climate control revision changed", code="revision_conflict")
            # Legacy v1 retains its historical fail-fast gate.  The negotiated
            # profile is a desired-intent protocol: it must durably remember a
            # valid user choice before a transient physical gate is evaluated.
            if request.reliability_profile is None:
                _require_action_allowed(snapshot, request)
            operation_id = self._new_operation_id()
            now = self._safe_now()
            # Latest-wins is deliberately limited to an intent that has not
            # crossed the physical boundary.  The successor must use the
            # predecessor's resulting revision, while any dispatched leaf is
            # immutable and can only be observed by polling.
            if request.reliability_profile is not None:
                predecessor = _pending_predecessor(
                    self._records_by_request.values(), request, snapshot
                )
                if predecessor is not None:
                    superseded = _superseded_receipt(
                        predecessor.receipt, operation_id, request.fingerprint, now
                    )
                    self._records_by_request[predecessor.request.request_id] = _StoredOperation(
                        predecessor.fingerprint, predecessor.request, superseded,
                        _reliable_dispatch_ledger(
                            superseded, "superseded_pre_dispatch"
                        ),
                    )
            next_control_revision = await self._reserve_control_revision_unlocked(
                request.expected_control_revision
            ) if request.reliability_profile is not None else self._control_revision + 1
            receipt = _pending_receipt(
                request,
                operation_id,
                now,
                snapshot=snapshot,
                resulting_control_revision=next_control_revision,
            )
            if preflight_scope is not None:
                receipt["action_snapshot"] = {
                    **dict(receipt.get("action_snapshot", {})),
                    "resolved_scope": preflight_scope,
                }
            if receipt.get("accepted") is True:
                self._control_revision = next_control_revision
                intent_key = _intent_key(request)
                self._desired_intents[intent_key] = _desired_intent(
                    request, self._control_revision,
                    self._desired_intents.get(intent_key),
                )
            pending_ledger = (
                _reliable_dispatch_ledger(receipt, "pending_dispatch")
                if request.reliability_profile == "climate_reliability_v1"
                else None
            )
            if request.reliability_profile == "climate_reliability_v1":
                self._reliable_scope_bindings[request.request_id] = (
                    _reliable_scope_binding(
                        request,
                        receipt,
                        self._reliable_scope_integrity_key,
                        self._last_reliability_metadata,
                        pending_ledger,
                    )
                )
            self._remember(request, receipt, pending_ledger)
            await self._async_save()
            frozen_scope = receipt.get("action_snapshot", {}).get("resolved_scope", {})
            physical_started = False
            native_terminal_checkpoint_failed = False
            dispatch_boundary: int | None = None
            pre_dispatch_metadata: dict[tuple[str, str], dict[str, object]] = {}
            result: object | None = None
            try:
                if request.reliability_profile is not None:
                    _require_action_allowed(snapshot, request)
                pre_dispatch_metadata = {
                    key: dict(value) for key, value in self._last_reliability_metadata.items()
                    if isinstance(value, Mapping)
                }
                dispatch_boundary = self._safe_now()
                if request.reliability_profile == "climate_reliability_v1":
                    started_ledger = _reliable_dispatch_ledger(
                        receipt,
                        "started",
                        dispatched_at=dispatch_boundary,
                        metadata=pre_dispatch_metadata,
                    )
                    self._remember(request, receipt, started_ledger)
                    await self._async_save()
                    physical_started = True
                result = await self._async_dispatch(request)
                final_snapshot = await self._snapshot_unlocked()
                final_ledger: dict[str, object] | None = None
                receipt = _receipt_from_contour_result(
                    request,
                    operation_id,
                    result,
                    created_at=now,
                    updated_at=self._safe_now(),
                    resulting_state_revision=final_snapshot["state_revision"],
                    snapshot=final_snapshot,
                    resulting_control_revision=self._control_revision,
                    reliability_metadata=self._last_reliability_metadata,
                    dispatched_at=dispatch_boundary,
                    pre_dispatch_metadata=pre_dispatch_metadata,
                    frozen_scope=frozen_scope,
                )
                if request.reliability_profile == "climate_reliability_v1":
                    exact_device_outcomes = _has_exact_reliable_device_outcomes(
                        result,
                        final_snapshot,
                        request,
                        frozen_scope=frozen_scope,
                        reliability_metadata=self._last_reliability_metadata,
                    )
                    has_device_outcomes = isinstance(
                        getattr(result, "device_outcomes", None), Mapping
                    )
                    command_count = getattr(result, "command_count", None)
                    accepted_count = getattr(result, "accepted_count", None)
                    trusted_terminal_blocked = (
                        exact_device_outcomes
                        and (command_count, accepted_count) == (0, 0)
                        and _has_trusted_terminal_blocked_outcomes(result)
                    )
                    if trusted_terminal_blocked:
                        # The runtime explicitly says that its frozen plan had
                        # no physical call. Keep that terminal 0/0 boundary;
                        # treating it as an aggregate gap would fabricate a
                        # retryable pending dispatch on the tablet.
                        receipt = _expired_pending_reliable_receipt(
                            receipt, self._safe_now(), blocked_immediately=True
                        )
                        receipt["resulting_control_revision"] = self._control_revision
                        final_ledger = _reliable_dispatch_ledger(
                            receipt, "blocked_before_dispatch"
                        )
                    elif has_device_outcomes and not exact_device_outcomes:
                        # A supplied but contradictory per-device map carries
                        # a possible physical boundary even when the aggregate
                        # counters say zero.  It must never be downgraded to a
                        # retryable pre-dispatch receipt.
                        receipt = _pending_receipt(
                            request, operation_id, now,
                            snapshot=final_snapshot,
                            resulting_control_revision=self._control_revision,
                        )
                        receipt["action_snapshot"] = {"resolved_scope": dict(frozen_scope)}
                        receipt = _reliable_receipt(
                            receipt, request, final_snapshot,
                            self._control_revision,
                        )
                        final_ledger = _reliable_dispatch_ledger(
                            receipt,
                            "started",
                            dispatched_at=dispatch_boundary,
                            metadata=pre_dispatch_metadata,
                        )
                    elif (
                        type(command_count) is int
                        and type(accepted_count) is int
                        and (command_count, accepted_count) == (0, 0)
                        and not has_device_outcomes
                    ):
                        # A zero-call aggregate cannot create a 1/1 leaf.  It
                        # remains a safe pre-dispatch receipt until an
                        # authoritative already-in-sync projection is available.
                        receipt = _pending_receipt(
                            request, operation_id, now,
                            snapshot=final_snapshot,
                            resulting_control_revision=self._control_revision,
                        )
                        receipt["action_snapshot"] = {"resolved_scope": dict(frozen_scope)}
                        receipt = _reliable_receipt(
                            receipt, request, final_snapshot,
                            self._control_revision,
                        )
                        final_ledger = _reliable_dispatch_ledger(
                            receipt, "pending_dispatch"
                        )
                    elif (
                        not has_device_outcomes
                        and (
                            _reliable_scope_size(
                                final_snapshot,
                                request,
                                frozen_scope=frozen_scope,
                            )
                            > 1
                            or type(command_count) is not int
                            or type(accepted_count) is not int
                            or (command_count, accepted_count) != (1, 1)
                        )
                    ):
                        # The executor crossed a physical boundary, but did not
                        # return a complete per-device acceptance map.  Keep the
                        # started checkpoint and an honest 0/0 public receipt;
                        # it is non-replayable and cannot fabricate acceptance.
                        receipt = _pending_receipt(
                            request, operation_id, now,
                            snapshot=final_snapshot,
                            resulting_control_revision=self._control_revision,
                        )
                        receipt["action_snapshot"] = {"resolved_scope": dict(frozen_scope)}
                        receipt = _reliable_receipt(
                            receipt, request, final_snapshot,
                            self._control_revision,
                        )
                        final_ledger = _reliable_dispatch_ledger(
                            receipt, "started",
                            dispatched_at=dispatch_boundary,
                            metadata=pre_dispatch_metadata,
                        )
                    elif receipt.get("status") not in {"confirmed", "pending", "partial"}:
                        receipt = _accepted_unverified_reliable_receipt(
                            request,
                            operation_id,
                            created_at=now,
                            updated_at=self._safe_now(),
                            snapshot=final_snapshot,
                            resulting_control_revision=self._control_revision,
                            reliability_metadata=self._last_reliability_metadata,
                            dispatched_at=dispatch_boundary,
                            pre_dispatch_metadata=pre_dispatch_metadata,
                        )
                        receipt["action_snapshot"] = {"resolved_scope": dict(frozen_scope)}
                        receipt = _reliable_receipt(
                            receipt, request, final_snapshot,
                            self._control_revision,
                            reliability_metadata=self._last_reliability_metadata,
                            dispatched_at=dispatch_boundary,
                            pre_dispatch_metadata=pre_dispatch_metadata,
                        )
                    if final_ledger is None:
                        ledger_state = (
                            "already_in_sync"
                            if _reliable_receipt_is_already_in_sync(receipt)
                            else (
                                "confirmed"
                                if receipt.get("status") == "confirmed"
                                else (
                                    "terminal_mixed"
                                    if receipt.get("final") is True
                                    else "accepted_unverified"
                                )
                            )
                        )
                        final_ledger = _reliable_dispatch_ledger(
                            receipt,
                            ledger_state,
                            dispatched_at=dispatch_boundary,
                            metadata=pre_dispatch_metadata,
                        )
            except ClimateTabletViolation as error:
                # The public action was syntactically valid and the desired
                # state is already durably reserved.  A closed physical gate
                # must not erase that intent or pretend that an HA call ran.
                if request.reliability_profile is not None:
                    if physical_started:
                        receipt = _ambiguous_started_reliable_receipt(
                            receipt, request, self._safe_now()
                        )
                        final_ledger = _reliable_dispatch_ledger(
                            receipt, "terminal_mixed",
                            dispatched_at=dispatch_boundary,
                            metadata=pre_dispatch_metadata,
                        )
                    else:
                        receipt = _pending_receipt(
                            request, operation_id, now, snapshot=snapshot,
                            resulting_control_revision=self._control_revision,
                        )
                        final_ledger = pending_ledger
                else:
                    receipt = _terminal_receipt(
                        request, operation_id, status="rejected",
                        reason="action_unsupported", message=str(error) or "Климатическое действие отклонено.",
                        created_at=now, updated_at=self._safe_now(), snapshot=snapshot,
                        resulting_control_revision=self._control_revision,
                    )
            except (ContourApplyViolation, TemporaryTemperatureViolation, HomeClimateTargetsViolation) as error:
                if (
                    request.reliability_profile == "climate_reliability_v1"
                    and getattr(error, "home_target_pre_dispatch", False)
                ):
                    self._desired_intents.pop(_intent_key(request), None)
                    receipt = _terminal_receipt(
                        request, operation_id, status="unavailable",
                        reason="action_unsupported",
                        message="Климатическая цель недоступна до отправки команды.",
                        created_at=now, updated_at=self._safe_now(), snapshot=snapshot,
                        resulting_control_revision=self._control_revision,
                    )
                    receipt["action_snapshot"] = {
                        **dict(receipt.get("action_snapshot", {})),
                        "resolved_scope": dict(frozen_scope),
                    }
                    receipt = _reliable_receipt(
                        receipt, request, snapshot, self._control_revision,
                        reliability_metadata=self._last_reliability_metadata,
                    )
                    native_terminal_checkpoint_failed = bool(
                        getattr(error, "home_target_terminal_persist_failed", False)
                    )
                    final_ledger = _reliable_dispatch_ledger(
                        receipt, "blocked_before_dispatch"
                    )
                elif request.reliability_profile == "climate_reliability_v1" and physical_started:
                    receipt = _ambiguous_started_reliable_receipt(
                        receipt, request, self._safe_now()
                    )
                    final_ledger = _reliable_dispatch_ledger(
                        receipt, "terminal_mixed",
                        dispatched_at=dispatch_boundary,
                        metadata=pre_dispatch_metadata,
                    )
                else:
                    receipt = _terminal_receipt(
                        request,
                        operation_id,
                        status="rejected",
                        reason="action_unsupported",
                        message=str(error) or "Климатическое действие отклонено.",
                        created_at=now,
                        updated_at=self._safe_now(),
                        snapshot=snapshot,
                        resulting_control_revision=self._control_revision,
                    )
            except Exception as error:
                if (
                    request.reliability_profile == "climate_reliability_v1"
                    and (
                        getattr(error, "reserved_tablet_pre_dispatch_conflict", False)
                        or getattr(error, "home_target_pre_dispatch", False)
                    )
                ):
                    # The runtime rechecked the shared revision after this
                    # coordinator released its lock and before it saved a
                    # contour or called HA.  This request owns no physical
                    # outcome, so close it as a conflict instead of retaining
                    # a misleading started/partial receipt.
                    self._desired_intents.pop(_intent_key(request), None)
                    receipt = _terminal_receipt(
                        request, operation_id, status="unavailable",
                        reason="action_unsupported",
                        message="Климатическая цель недоступна до отправки команды.",
                        created_at=now, updated_at=self._safe_now(), snapshot=snapshot,
                        resulting_control_revision=self._control_revision,
                    )
                    receipt["action_snapshot"] = {
                        **dict(receipt.get("action_snapshot", {})),
                        "resolved_scope": dict(frozen_scope),
                    }
                    receipt = _reliable_receipt(
                        receipt, request, snapshot, self._control_revision,
                        reliability_metadata=self._last_reliability_metadata,
                    )
                    native_terminal_checkpoint_failed = bool(
                        getattr(error, "home_target_terminal_persist_failed", False)
                    )
                    final_ledger = _reliable_dispatch_ledger(
                        receipt, "blocked_before_dispatch"
                    )
                elif request.reliability_profile == "climate_reliability_v1" and physical_started:
                    receipt = _ambiguous_started_reliable_receipt(
                        receipt, request, self._safe_now()
                    )
                    final_ledger = _reliable_dispatch_ledger(
                        receipt, "terminal_mixed",
                        dispatched_at=dispatch_boundary,
                        metadata=pre_dispatch_metadata,
                    )
                elif request.reliability_profile == "climate_reliability_v1":
                    # The checkpoint itself was not saved.  Do not let a
                    # catch-all path replace the already durable 0/0 receipt
                    # with an internally inconsistent terminal result.
                    raise
                else:
                    receipt = _terminal_receipt(
                        request,
                        operation_id,
                        status="unavailable",
                        reason="internal_error",
                        message="Не удалось надёжно получить результат климатической команды.",
                        created_at=now,
                        updated_at=self._safe_now(),
                        snapshot=snapshot,
                        resulting_control_revision=self._control_revision,
                    )
            if _legacy_correlation_id is not None:
                self._legacy_home_execution_facts[_legacy_correlation_id] = (
                    _legacy_home_execution_fact(receipt, request, result)
                )
            self._remember(
                request, receipt,
                final_ledger if request.reliability_profile == "climate_reliability_v1" and physical_started else pending_ledger,
            )
            await self._async_save()
            if native_terminal_checkpoint_failed:
                # The tablet receipt is durable, but the paired native record
                # could not be closed. Do not issue another physical command
                # from this process against that ambiguous native history.
                self._persistence_failed = True
            return dict(receipt)

    async def async_execute_legacy_home_targets(
        self,
        *,
        request_id: str,
        correlation_id: str,
        parameters: Mapping[str, object],
    ) -> dict[str, object]:
        """Adapt the legacy route without making a duplicate read a new revision."""

        if not isinstance(parameters, Mapping):
            raise ClimateTabletViolation("legacy home climate parameters are invalid")
        canonical_parameters = dict(parameters)
        async with self._lock:
            self._require_reliability_health()
            if self._persistence_failed:
                raise ClimateTabletUnavailable("climate persistence requires restart")
            # Resolve the compatibility identity before a fresh runtime read.
            # A retry must be a pure receipt lookup even if the runtime has
            # become unavailable after the first physical operation.
            prior = self._records_by_request.get(request_id)
            if prior is not None:
                previous = prior.request
                if (
                    previous.action != "set_home_targets"
                    or previous.correlation_id != correlation_id
                    or previous.parameters != canonical_parameters
                ):
                    raise ClimateTabletViolation(
                        "request id was already used for another climate action",
                        code="revision_conflict",
                    )
                fact = self._legacy_home_execution_facts.get(correlation_id)
                if not _legacy_home_execution_fact_matches_record(
                    correlation_id, fact, prior
                ):
                    raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
                return {
                    **prior.receipt, "duplicate": True,
                    "__legacy_execution_fact__": dict(fact),
                }
            fact = self._legacy_home_execution_facts.get(correlation_id)
            if fact is not None:
                record = self._records_by_request.get(fact["request_id"])
                if not _legacy_home_execution_fact_matches_record(
                    correlation_id, fact, record
                ):
                    raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
                if fact["parameters_fingerprint"] != _canonical_fingerprint(canonical_parameters):
                    raise ClimateTabletViolation(
                        "correlation id was already used for other climate targets",
                        code="revision_conflict",
                    )
                return {**record.receipt, "duplicate": True, "__legacy_execution_fact__": dict(fact)}
            snapshot = await self._snapshot_unlocked()
            payload = {
                "contract": {
                    "name": CLIMATE_ACTION_CONTRACT_NAME,
                    "version": CLIMATE_TABLET_CONTRACT_VERSION,
                },
                "request_id": request_id,
                "correlation_id": correlation_id,
                "expected_state_revision": snapshot["state_revision"],
                "expected_control_revision": snapshot["control_revision"],
                "reliability_profile": "climate_reliability_v1",
                "action": "set_home_targets",
                "room_id": None,
                "parameters": canonical_parameters,
            }
            # `async_execute` consumes this typed command under the lock that
            # produced its revision.  This keeps duplicate identity, pending
            # detection and reservation one atomic service operation.
            receipt = await self.async_execute(
                payload, _lock_held=True, _legacy_fail_fast=True,
                _legacy_correlation_id=correlation_id,
            )
            fact = self._legacy_home_execution_facts.get(correlation_id)
            record = self._records_by_request.get(request_id)
            if not _legacy_home_execution_fact_matches_record(correlation_id, fact, record):
                raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
            return {**receipt, "__legacy_execution_fact__": dict(fact)}

    async def async_operation(self, operation_id: str) -> dict[str, object]:
        """Return one persisted receipt without repeating its physical command."""

        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            raise ClimateTabletOperationNotFound(operation_id)
        async with self._lock:
            if self._maintenance_unhealthy():
                raise ClimateTabletUnavailable("climate operation persistence is unavailable")
            await self._expire_pending_unlocked()
            await self._refresh_pending_unlocked()
            request_id = self._request_by_operation.get(operation_id)
            if request_id is None:
                raise ClimateTabletOperationNotFound(operation_id)
            record = self._records_by_request[request_id]
            return dict(record.receipt)

    async def async_recover_room(
        self, room_id: str, payload: object
    ) -> dict[str, object]:
        """Reject recovery unless the runtime can prove the complete preflight.

        Recovery is deliberately not synthesized from client supplied device
        IDs or a stale screen snapshot.  A later runtime implementation must
        provide the signed, authoritative desired snapshot before this surface
        may dispatch anything.
        """

        if not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None:
            raise ClimateTabletViolation("climate recovery room is invalid")
        if not isinstance(payload, Mapping):
            raise ClimateTabletViolation("climate recovery request is invalid")
        async with self._lock:
            self._require_reliability_health()
            # Expire unresolved physical boundaries before replay lookup. A
            # stale receipt must not stay indefinitely successful merely
            # because the caller repeats its original request id.
            await self._expire_recoveries_unlocked()
            # A durable replay is resolved before validating the current
            # preflight.  The original accepted request necessarily carries
            # the old token after its own revision bump, yet it must still be
            # returned without another dispatch.
            candidate_id = payload.get("request_id")
            previous = self._recoveries_by_request.get(candidate_id) if isinstance(candidate_id, str) else None
            if previous is not None:
                prior_receipt = previous["receipt"]
                frozen_snapshot = prior_receipt.get("request_snapshot")
                frozen_request = (
                    frozen_snapshot.get("request")
                    if isinstance(frozen_snapshot, Mapping) else None
                )
                candidate_fingerprint = _recovery_request_fingerprint(payload)
                if (
                    prior_receipt.get("room_id") != room_id
                    or payload.get("request_fingerprint") != candidate_fingerprint
                    or candidate_fingerprint != prior_receipt.get("request_fingerprint")
                    or not isinstance(frozen_request, Mapping)
                    or dict(frozen_request) != dict(payload)
                ):
                    raise ClimateTabletViolation("recovery request id was already used", code="revision_conflict")
                return {**prior_receipt, "duplicate": True}
            token = payload.get("snapshot_token")
            stored = self._recovery_preflights.get(token) if isinstance(token, str) else None
            if stored is None or stored.get("expires_at", 0) < self._safe_now():
                raise ClimateTabletViolation("climate recovery preflight changed", code="revision_conflict")
            preflight = stored.get("preflight")
            if not isinstance(preflight, Mapping) or preflight.get("room_id") != room_id:
                raise ClimateTabletViolation("climate recovery preflight changed", code="revision_conflict")
            preflight = dict(preflight)
            request = _parse_recovery_request(payload, room_id, preflight)
            # Revision and token protect the preflight, but they must not let
            # a fresh request bypass a prior physical call with unknown
            # outcome.  The private ledger is the durable lock authority.
            selected_ids = set(request["selected_device_ids"])
            for existing in self._recoveries_by_request.values():
                existing_receipt = existing["receipt"]
                if existing_receipt.get("room_id") != room_id:
                    continue
                overlap = selected_ids & _recovery_unresolved_device_ids(existing["ledger"])
                if not overlap:
                    continue
                old_desired = existing_receipt.get("desired_snapshot")
                old_scope = existing_receipt.get("resolved_device_ids")
                same_desired = (
                    isinstance(old_desired, Mapping)
                    and isinstance(old_scope, list)
                    and selected_ids == set(old_scope)
                    and all(old_desired.get(device_id) == preflight["desired_snapshot"].get(device_id)
                            for device_id in selected_ids)
                )
                if same_desired and existing_receipt.get("request_id") == request["request_id"]:
                    return {**existing_receipt, "duplicate": True}
                raise ClimateTabletViolation("recovery is already in progress", code="revision_conflict")
            # Reserve before any runtime call. This durable boundary also prevents
            # two request IDs from racing the same room/revision/token scope.
            scope_key = (room_id, preflight["control_revision"], preflight["snapshot_token"])
            if any((item["receipt"].get("room_id"), item["receipt"].get("expected_control_revision"), item["receipt"].get("snapshot_token")) == scope_key
                   and item["receipt"].get("final") is False for item in self._recoveries_by_request.values()):
                raise ClimateTabletViolation("recovery is already in progress", code="revision_conflict")
            # Keep recovery replay bounded independently from ordinary action
            # history.  Only terminal records are disposable: unresolved
            # dispatch boundaries are physical safety locks, not cache rows.
            if len(self._recoveries_by_request) >= MAX_CLIMATE_OPERATION_RECORDS:
                self._prune_oldest_final_recovery()
            if len(self._recoveries_by_request) >= MAX_CLIMATE_OPERATION_RECORDS:
                raise ClimateTabletUnavailable("climate recovery history is full")
            now = self._safe_now()
            operation_id = self._new_recovery_operation_id()
            ledger = {device_id: _recovery_leaf("pending_dispatch")
                      for device_id in request["selected_device_ids"]}
            # Recovery acceptance is a durable desired-state transition, not a
            # side effect of successful read-back.  Advance exactly once with
            # the reservation before any physical call so restarts, polls and
            # later preflights share one authoritative revision.
            self._control_revision = await self._reserve_control_revision_unlocked(
                preflight["control_revision"]
            )
            receipt = _recovery_receipt(request, preflight, operation_id, now, ledger)
            self._recoveries_by_request[request["request_id"]] = {
                "receipt": receipt, "ledger": ledger, "preflight": dict(preflight),
            }
            self._recovery_by_operation[operation_id] = request["request_id"]
            # The opaque preflight is single-use.  Its immutable echo remains
            # in the receipt, while a different request cannot reuse it after
            # the durable reservation has advanced the control revision.
            self._recovery_preflights.pop(token, None)
            await self._async_save()
            record = self._recoveries_by_request[request["request_id"]]
            for device_id in request["selected_device_ids"]:
                # A recovery token is not a licence to act after the runtime
                # changed.  Re-read the authoritative gates before every
                # individual physical boundary.
                try:
                    current = await self._snapshot_unlocked()
                    current_preflight = _recovery_device_preflight(current, room_id, device_id)
                    current_desired = {
                        **current_preflight["desired_snapshot"].get(device_id, {}),
                        "source_observed_at": self._last_reliability_metadata.get(
                            (room_id, device_id), {}
                        ).get("source_observed_at"),
                    }
                    if current_desired != preflight["desired_snapshot"].get(device_id):
                        raise ClimateTabletViolation("recovery device desired state changed", code="revision_conflict")
                except Exception:
                    record["ledger"][device_id] = _recovery_leaf("blocked_before_dispatch")
                    record["receipt"] = _recovery_receipt(request, preflight, operation_id, now, record["ledger"])
                    await self._async_save()
                    continue
                room = next((item for item in current.get("rooms", []) if isinstance(item, Mapping) and item.get("id") == room_id), None)
                devices = room.get("devices") if isinstance(room, Mapping) else None
                device = next((item for item in devices if isinstance(item, Mapping) and item.get("id") == device_id), None) if isinstance(devices, list) else None
                if isinstance(device, Mapping) and device.get("available") is not True:
                    # Ownership is durable even while the device is offline.
                    # No executor/read-back boundary is crossed here.
                    try:
                        recover_offline = getattr(self._runtime, "async_recover_offline_device", None)
                        if not callable(recover_offline):
                            raise ClimateTabletUnavailable("climate recovery ownership boundary is unavailable")
                        self._require_reliability_health()
                        await recover_offline(
                            room_id=room_id, device_id=device_id,
                            expected_control_revision=self._control_revision,
                        )
                    except Exception:
                        record["ledger"][device_id] = _recovery_leaf("blocked_before_dispatch")
                        record["receipt"] = _recovery_receipt(request, preflight, operation_id, now, record["ledger"])
                        await self._async_save()
                        continue
                    record["ledger"][device_id] = _recovery_leaf("deferred_offline")
                    record["receipt"] = _recovery_receipt(request, preflight, operation_id, now, record["ledger"])
                    await self._async_save()
                    continue
                # A leaf is written as started before the physical boundary.
                # After a crash it is unknown, never a candidate for replay.
                dispatched_at = self._safe_now()
                record["ledger"][device_id] = _recovery_leaf("started", dispatched_at=dispatched_at)
                record["receipt"] = _recovery_receipt(request, preflight, operation_id, now, record["ledger"])
                await self._async_save()
                try:
                    self._require_reliability_health()
                    await self._runtime.async_recover_device(
                        request_id=request["request_id"], room_id=room_id,
                        device_id=device_id,
                        desired=preflight["desired_snapshot"][device_id],
                        expected_control_revision=self._control_revision,
                    )
                except Exception as error:
                    record["ledger"][device_id] = (
                        _recovery_leaf("blocked_before_dispatch")
                        if getattr(error, "recovery_pre_dispatch", False)
                        else _recovery_leaf("accepted_unverified", dispatched_at=dispatched_at)
                        if getattr(error, "recovery_accepted_after_dispatch", False)
                        else _recovery_leaf("dispatched_not_accepted", dispatched_at=dispatched_at)
                    )
                    record["receipt"] = _recovery_receipt(request, preflight, operation_id, now, record["ledger"])
                    await self._async_save()
                    continue
                # Persist acceptance before any read-back.  A persistence
                # failure here leaves the previous durable `started` state,
                # which is conservatively non-replayable after restart.
                record["ledger"][device_id] = _recovery_leaf("accepted_unverified", dispatched_at=dispatched_at)
                record["receipt"] = _recovery_receipt(request, preflight, operation_id, now, record["ledger"])
                await self._async_save()
                observed_at: int | None = None
                try:
                    observed = await self._snapshot_unlocked()
                    observed_at = self._last_reliability_metadata.get(
                        (room_id, device_id), {}
                    ).get("source_observed_at")
                    if _recovery_device_matches(
                        observed,
                        request,
                        device_id,
                        preflight["desired_snapshot"][device_id],
                        dispatched_at,
                        observed_at=observed_at,
                    ):
                        record["ledger"][device_id] = _recovery_leaf("applied", dispatched_at=dispatched_at)
                except Exception:
                    pass
                observed_by_device = _recovery_receipt_observed_at(record["receipt"])
                if type(observed_at) is int:
                    observed_by_device[device_id] = observed_at
                record["receipt"] = _recovery_receipt(
                    request, preflight, operation_id, now, record["ledger"],
                    observed_at_by_device=observed_by_device,
                )
                await self._async_save()
            return dict(record["receipt"])

    async def async_recovery_v2_preflight(self, room_id: str) -> dict[str, object]:
        """Return a server-authored recovery v2 scope without dispatch."""
        async with self._lock:
            self._require_reliability_health()
            previous_preflights = dict(self._recovery_preflights)
            if self._prune_recovery_preflights_unlocked():
                try:
                    await self._async_save()
                except Exception:
                    self._recovery_preflights = previous_preflights
                    raise
            snapshot = await self._snapshot_unlocked()
            preflight = _recovery_preflight(snapshot, room_id)
            rooms = snapshot.get("rooms")
            room = next((item for item in rooms if isinstance(item, Mapping) and item.get("id") == room_id), None) if isinstance(rooms, list) else None
            desired = {
                device_id: {**value, "source_observed_at": self._last_reliability_metadata.get((room_id, device_id), {}).get("source_observed_at")}
                for device_id, value in preflight["desired_snapshot"].items()
            }
            for device_id in preflight["available_device_ids"]:
                source_observed_at = desired[device_id]["source_observed_at"]
                if type(source_observed_at) is not int or not 0 <= source_observed_at <= 9_007_199_254_740_991:
                    raise ClimateTabletViolation(
                        "climate recovery source evidence is unavailable",
                        code="action_unsupported",
                    )
            fingerprint = _canonical_fingerprint({"room_id": room_id, "control_revision": preflight["control_revision"], "resolved_device_ids": preflight["resolved_device_ids"], "desired_snapshot": desired})
            token = f"recovery.v2.{secrets.token_hex(16)}"
            result = {"contract": {"name": "hausman-hub-climate-room-recovery-v2-preflight", "version": 2},
                    "room_id": room_id, "control_revision": preflight["control_revision"],
                    "desired_snapshot": desired, "desired_snapshot_fingerprint": fingerprint,
                    "resolved_device_ids": preflight["resolved_device_ids"],
                    "snapshot_token": token}
            # Supersede only a not-yet-used preflight for this exact room and
            # revision. A bounded collection prevents a local caller from
            # poisoning durable storage with arbitrary GET retries.
            for previous, item in tuple(self._recovery_preflights.items()):
                saved = item.get("preflight") if isinstance(item, Mapping) else None
                if isinstance(saved, Mapping) and saved.get("room_id") == room_id and saved.get("control_revision") == preflight["control_revision"]:
                    self._recovery_preflights.pop(previous, None)
            self._prune_recovery_preflights_unlocked()
            self._recovery_preflights[token] = {"preflight": {**preflight, "desired_snapshot": desired,
                "preflight_snapshot_fingerprint": fingerprint, "snapshot_token": token}, "expires_at": self._safe_now() + RECOVERY_PREFLIGHT_TTL_MS}
            self._prune_recovery_preflights_unlocked()
            try:
                await self._async_save()
            except Exception:
                self._recovery_preflights = previous_preflights
                raise
            return result

    async def async_recovery_operation(self, operation_id: str) -> dict[str, object]:
        """No recovery receipt exists until authoritative preflight is enabled."""

        async with self._lock:
            self._require_reliability_health()
            await self._expire_recoveries_unlocked()
            request_id = self._recovery_by_operation.get(operation_id)
            if request_id is None:
                raise ClimateTabletOperationNotFound(operation_id)
            record = self._recoveries_by_request[request_id]
            await self._refresh_recovery_read_back_unlocked(record)
            return dict(record["receipt"])

    async def _snapshot_unlocked(self) -> dict[str, object]:
        mode = _climate_mode(self._runtime)
        if mode == "disabled":
            projection = climate_tablet_snapshot(
                None, climate_mode=mode, generated_at=self._safe_now()
            )
            projection["control_revision"] = self._control_revision
            return _with_reliability_projection(projection, self._desired_intents, self._control_revision)
        try:
            home = await self._runtime.async_public_snapshot()
        except Exception as error:
            raise ClimateTabletUnavailable("climate runtime is unavailable") from error
        active = tuple(
            _operation_summary(record.receipt)
            for record in self._records_by_request.values()
            if record.receipt.get("final") is False
        )
        confirmed = tuple(
            _operation_summary(record.receipt)
            for record in self._records_by_request.values()
            if record.receipt.get("status") == "confirmed"
        )
        projection = climate_tablet_snapshot(
            home,
            climate_mode=mode,
            active_operations=active,
            confirmed_operations=confirmed,
        )
        projection["control_revision"] = self._control_revision
        metadata = _reliability_metadata(home)
        private_metadata = getattr(self._runtime, "async_recovery_private_metadata", None)
        if callable(private_metadata):
            try:
                supplied = await private_metadata()
            except Exception as error:
                raise ClimateTabletUnavailable("climate recovery proof is unavailable") from error
            if not isinstance(supplied, Mapping):
                raise ClimateTabletUnavailable("climate recovery proof is invalid")
            for key, value in supplied.items():
                if (
                    not isinstance(key, tuple)
                    or len(key) != 2
                    or not all(isinstance(item, str) for item in key)
                    or not isinstance(value, Mapping)
                ):
                    raise ClimateTabletUnavailable("climate recovery proof is invalid")
                metadata[key] = {
                    **metadata.get(key, {}),
                    **_validate_private_recovery_metadata(value),
                }
        self._last_reliability_metadata = metadata
        return _with_reliability_projection(
            projection, self._desired_intents, self._control_revision,
            self._last_reliability_metadata,
        )

    async def _async_dispatch(self, request: ClimateTabletActionRequest) -> object:
        # Reliability reservation belongs to this service.  Native runtime
        # methods receive an explicit already-reserved handoff so a tablet
        # request cannot advance the shared token a second time.
        if request.reliability_profile is not None:
            self._require_reliability_health()
        reserved = getattr(self._runtime, "async_execute_reserved_tablet_action", None)
        if request.reliability_profile is not None and callable(reserved):
            result = await reserved(
                action=request.action,
                room_id=request.room_id,
                parameters=dict(request.parameters),
                request_id=request.request_id,
                correlation_id=request.correlation_id,
                expected_control_revision=request.expected_control_revision,
                resulting_control_revision=self._control_revision,
                local_now=self._local_now(),
                tablet_request_fingerprint=request.fingerprint,
                tablet_action=request.action,
                tablet_parameters=dict(request.parameters),
            )
            return result
        if request.action == "set_home_targets":
            return await self._runtime.async_home_climate_targets(
                {
                    "correlation_id": request.correlation_id,
                    "request_id": request.request_id,
                    "contour_id": "climate",
                    "target_temperature": request.parameters.get("target_temperature"),
                    "target_humidity": request.parameters.get("target_humidity"),
                    "confirm": True,
                }
            )
        if request.action == "synchronize_home":
            return await self._runtime.async_synchronize_climate()
        if request.action == "set_room_mode":
            return await self._runtime.async_set_room_mode(
                request.room_id,
                request.parameters.get("mode"),
            )
        if request.action == "set_device_mode":
            return await self._runtime.async_set_device_mode(
                request.room_id,
                request.parameters.get("device_id"),
                request.parameters.get("mode"),
            )
        if request.action == "set_room_humidity_target":
            return await self._runtime.async_room_humidity_target(
                request_id=request.request_id,
                room_id=request.room_id,
                target_humidity=request.parameters.get("target_humidity"),
            )
        if request.action == "set_room_min_target":
            return await self._runtime.async_room_minimum_temperature(
                request_id=request.request_id,
                room_id=request.room_id,
                minimum_temperature=request.parameters.get("minimum_temperature"),
            )
        if request.action == "set_room_target_strategy":
            return await self._runtime.async_room_target_strategy(
                request_id=request.request_id,
                room_id=request.room_id,
                target_strategy=request.parameters.get("target_strategy"),
            )
        if request.action == "turn_room_off":
            return await self._runtime.async_turn_room_off(
                request_id=request.request_id, room_id=request.room_id
            )
        return await self._runtime.async_temporary_temperature(
            {
                "correlation_id": request.correlation_id,
                "request_id": request.request_id,
                "contour_id": "climate",
                "room_id": request.room_id,
                "action": "set" if request.action == "set_room_target" else "clear",
                "target_temperature": request.parameters.get("target_temperature"),
                "confirm": True,
            },
            self._local_now(),
        )

    def _new_operation_id(self) -> str:
        operation_id = self._operation_id_factory()
        if (
            not isinstance(operation_id, str)
            or _OPERATION_ID.fullmatch(operation_id) is None
            or operation_id in self._request_by_operation
        ):
            raise ClimateTabletUnavailable("climate operation id is unsafe")
        return operation_id

    def _remember(
        self,
        request: ClimateTabletActionRequest,
        receipt: dict[str, object],
        dispatch_ledger: dict[str, object] | None = None,
    ) -> None:
        normalized = _validate_receipt(receipt)
        self._records_by_request[request.request_id] = _StoredOperation(
            request.fingerprint, request, normalized, dispatch_ledger
        )
        self._request_by_operation[normalized["operation_id"]] = request.request_id

    async def _async_save(self) -> None:
        records = []
        for request_id, record in self._records_by_request.items():
            item: dict[str, object] = {
                "request_id": request_id,
                "fingerprint": record.fingerprint,
                "request": _request_payload(record.request),
                "receipt": record.receipt,
            }
            if record.dispatch_ledger is not None:
                item["dispatch_ledger"] = record.dispatch_ledger
            records.append(item)
        save_bindings = getattr(self._store, "async_save_reliable_scope_bindings", None)
        if not callable(save_bindings):
            raise ClimateTabletUnavailable("reliable climate scope storage is unavailable")
        main_payload = {
            "version": 6,
            "records": records,
            "recoveries": list(self._recoveries_by_request.values()),
            "control_revision": self._control_revision,
            "desired_intents": self._desired_intents,
            "recovery_preflights": [
                {"token": token, "preflight": item["preflight"], "expires_at": item["expires_at"]}
                for token, item in self._recovery_preflights.items()
            ],
        }
        prepared_bindings = json.loads(json.dumps(self._reliable_scope_bindings))
        for request_id, record in self._records_by_request.items():
            if record.request.reliability_profile != "climate_reliability_v1":
                continue
            binding = prepared_bindings.get(request_id)
            if not isinstance(binding, Mapping):
                raise ClimateTabletUnavailable("climate reliable scope is invalid")
            prepared_bindings[request_id] = _binding_with_checkpoint(
                binding, record.receipt, record.dispatch_ledger,
                self._reliable_scope_integrity_key,
            )
        prepared_bindings["__tablet_state__"] = _tablet_state_with_checkpoint(
            prepared_bindings.get("__tablet_state__"), main_payload,
            self._reliable_scope_integrity_key,
        )
        legacy_facts = {
            correlation_id: fact
            for correlation_id, fact in self._legacy_home_execution_facts.items()
            if _legacy_home_execution_fact_matches_record(
                correlation_id, fact,
                self._records_by_request.get(fact.get("request_id")),
            )
        }
        self._legacy_home_execution_facts = legacy_facts
        prepared_bindings["__legacy_home_execution_facts__"] = legacy_facts
        try:
            await save_bindings(prepared_bindings)
        except Exception:
            self._persistence_failed = True
            raise
        # Save scope provenance first.  An orphan binding is harmless, but an
        # unbound main record cannot be restored safely.
        try:
            await self._store.async_save(main_payload)
        except Exception:
            self._persistence_failed = True
            raise
        final_bindings = {
            request_id: _binding_with_only_current_checkpoint(
                prepared_bindings[request_id], record.receipt,
                record.dispatch_ledger, self._reliable_scope_integrity_key,
            )
            for request_id, record in self._records_by_request.items()
            if record.request.reliability_profile == "climate_reliability_v1"
        }
        final_bindings["__tablet_state__"] = _tablet_state_with_only_current_checkpoint(
            prepared_bindings["__tablet_state__"], main_payload,
            self._reliable_scope_integrity_key,
        )
        final_bindings["__legacy_home_execution_facts__"] = legacy_facts
        try:
            await save_bindings(final_bindings)
        except Exception:
            self._persistence_failed = True
            raise
        self._reliable_scope_bindings = final_bindings
        self._reliable_scope_binding_cleanup.clear()

    async def _reserve_control_revision_unlocked(self, expected: object) -> int:
        """Use the shared store coordinator when available, with test fallback."""

        self._require_reliability_health()
        if (
            not is_control_revision(expected)
            or not is_control_revision(self._control_revision)
            or expected != self._control_revision
            or expected >= MAX_JS_SAFE_INTEGER
        ):
            raise ClimateTabletViolation("climate control revision changed", code="revision_conflict")
        reserve = getattr(self._store, "async_reserve_control_revision", None)
        if not callable(reserve):
            return expected + 1
        try:
            reserved = await reserve(expected)
        except ClimateOperationRevisionConflict as error:
            raise ClimateTabletViolation(
                "climate control revision changed", code="revision_conflict"
            ) from error
        except Exception as error:
            self._persistence_failed = True
            raise ClimateTabletUnavailable(
                "climate control revision reservation is unavailable"
            ) from error
        if not is_control_revision(reserved) or reserved != expected + 1:
            raise ClimateTabletUnavailable("climate control revision reservation is invalid")
        return reserved

    async def _sync_control_revision_unlocked(self) -> int:
        """Read the coordinator token before exposing or validating a request."""
        current = getattr(self._store, "async_current_control_revision", None)
        if not callable(current):
            return self._control_revision
        try:
            value = await current()
        except Exception as error:
            raise ClimateTabletUnavailable("climate control revision is unavailable") from error
        if not is_control_revision(value):
            raise ClimateTabletUnavailable("climate control revision is invalid")
        self._control_revision = value
        return value

    async def _refresh_recovery_read_back_unlocked(self, record: dict[str, object]) -> None:
        """Resolve only by authoritative read-back, never by redispatch."""
        ledger = record.get("ledger")
        receipt = record.get("receipt")
        if not isinstance(ledger, dict) or not isinstance(receipt, Mapping):
            raise ClimateTabletUnavailable("stored climate recovery is invalid")
        unresolved = _recovery_unresolved_device_ids(ledger)
        if not unresolved:
            return
        try:
            snapshot = await self._snapshot_unlocked()
        except ClimateTabletUnavailable:
            return
        desired = receipt.get("desired_snapshot")
        request_payload = receipt.get("request_snapshot")
        original = request_payload.get("request") if isinstance(request_payload, Mapping) else None
        if not isinstance(desired, Mapping) or not isinstance(original, Mapping):
            raise ClimateTabletUnavailable("stored climate recovery is invalid")
        request = {
            "request_id": receipt.get("request_id"), "correlation_id": receipt.get("correlation_id"),
            "correlation_policy": request_payload.get("correlation_policy"), "request_fingerprint": receipt.get("request_fingerprint"),
            "request": dict(original), "selected_device_ids": list(receipt.get("resolved_device_ids", [])),
            "room_id": receipt.get("room_id"),
        }
        changed = False
        for device_id in unresolved:
            device_desired = desired.get(device_id)
            leaf = ledger.get(device_id)
            dispatched_at = leaf.get("dispatched_at") if isinstance(leaf, Mapping) else None
            # A reservation alone is not evidence that an HA call crossed the
            # physical boundary.  In particular, a crash before the durable
            # `started` checkpoint must remain 0/0 pending and cannot become
            # applied merely because a later observation happens to match.
            if type(dispatched_at) is not int:
                continue
            if (isinstance(leaf, Mapping)
                    and leaf.get("ledger_state", leaf.get("execution_state")) == "accepted_unverified"
                    and isinstance(device_desired, Mapping) and _recovery_device_matches(
                snapshot, request, device_id, device_desired,
                dispatched_at,
                observed_at=self._last_reliability_metadata.get(
                    (receipt.get("room_id"), device_id), {}
                ).get("source_observed_at"),
            )):
                ledger[device_id] = _recovery_leaf("applied", dispatched_at=dispatched_at if type(dispatched_at) is int else None)
                changed = True
        if not changed:
            return
        preflight = {
            "control_revision": receipt.get("expected_control_revision"),
            "desired_snapshot": dict(desired),
            "preflight_snapshot_fingerprint": receipt.get("preflight_snapshot_fingerprint"),
            "snapshot_token": receipt.get("snapshot_token"),
        }
        observed_by_device = _recovery_receipt_observed_at(receipt)
        for device_id, leaf in ledger.items():
            if isinstance(leaf, Mapping) and leaf.get("ledger_state", leaf.get("execution_state")) == "applied":
                value = self._last_reliability_metadata.get((receipt.get("room_id"), device_id), {}).get("source_observed_at")
                if type(value) is int:
                    observed_by_device[device_id] = value
        record["receipt"] = _recovery_receipt(
            request, preflight, receipt.get("operation_id"), receipt.get("created_at"), ledger,
            observed_at_by_device=observed_by_device,
        )
        await self._async_save()

    async def _expire_pending_unlocked(self) -> None:
        now = self._safe_now()
        changed = False
        for request_id, record in tuple(self._records_by_request.items()):
            receipt = record.receipt
            if receipt.get("final") is not False or now < receipt["expires_at"]:
                continue
            # The private dispatch ledger distinguishes a saved intent from a
            # physical attempt.  A public timeout receipt cannot express that
            # distinction, so do not manufacture a 1/1 timeout here.
            if record.request.reliability_profile == "climate_reliability_v1":
                ledger = record.dispatch_ledger
                if isinstance(ledger, Mapping) and ledger.get("state") == "pending_dispatch":
                    expired = _expired_pending_reliable_receipt(receipt, now)
                    self._records_by_request[request_id] = _StoredOperation(
                        record.fingerprint, record.request, expired,
                        _reliable_dispatch_ledger(expired, "pending_expired"),
                    )
                    changed = True
                elif isinstance(ledger, Mapping) and ledger.get("state") == "accepted_unverified":
                    timed_out = _expire_accepted_reliable_receipt(receipt, now)
                    baseline = _metadata_from_reliable_dispatch_ledger(ledger)
                    self._records_by_request[request_id] = _StoredOperation(
                        record.fingerprint, record.request, timed_out,
                        _reliable_dispatch_ledger(
                            timed_out, "accepted_timeout",
                            dispatched_at=ledger.get("dispatched_at"),
                            metadata=baseline,
                        ),
                    )
                    changed = True
                elif isinstance(ledger, Mapping) and ledger.get("state") == "started":
                    baseline = _metadata_from_reliable_dispatch_ledger(ledger)
                    ambiguous = _ambiguous_started_reliable_receipt(
                        receipt, record.request, now
                    )
                    self._records_by_request[request_id] = _StoredOperation(
                        record.fingerprint, record.request, ambiguous,
                        _reliable_dispatch_ledger(
                            ambiguous, "terminal_mixed",
                            dispatched_at=ledger.get("dispatched_at"),
                            metadata=baseline,
                        ),
                    )
                    changed = True
                continue
            timed_out = _terminal_receipt(
                record.request,
                receipt["operation_id"],
                status="timed_out",
                reason="confirmation_timeout",
                message="Устройство не подтвердило новое состояние за отведённое время.",
                created_at=receipt["created_at"],
                updated_at=now,
                snapshot=_snapshot_from_reliable_scope(receipt),
                resulting_control_revision=receipt.get("resulting_control_revision"),
            )
            if record.request.reliability_profile == "climate_reliability_v1":
                # The timeout snapshot intentionally contains only frozen IDs,
                # not live kind metadata.  Preserve the original resolved
                # scope instead of recalculating it from that reduced view.
                timed_out["action_snapshot"] = receipt.get("action_snapshot")
                timed_out = _reliable_receipt(
                    timed_out,
                    record.request,
                    _snapshot_from_reliable_scope(receipt),
                    receipt.get("resulting_control_revision"),
                )
            self._records_by_request[request_id] = _StoredOperation(
                record.fingerprint, record.request, timed_out
            )
            changed = True
        if changed:
            await self._async_save()

    async def _expire_recoveries_unlocked(self) -> None:
        """Expire disposable preflights, never invent a v2 timeout outcome.

        A v2 physical boundary with no new device proof remains an unknown
        durable result.  The public v2 vocabulary intentionally has no
        fabricated ``timed_out`` leaf, and a later poll cannot redispatch it.
        """
        if self._prune_recovery_preflights_unlocked():
            await self._async_save()

    def _prune_recovery_preflights_unlocked(self) -> bool:
        now = self._safe_now()
        expired = [token for token, item in self._recovery_preflights.items()
                   if not isinstance(item, Mapping) or type(item.get("expires_at")) is not int or item["expires_at"] <= now]
        for token in expired:
            self._recovery_preflights.pop(token, None)
        overflow = len(self._recovery_preflights) - MAX_CLIMATE_OPERATION_RECORDS
        if overflow > 0:
            oldest = sorted(self._recovery_preflights, key=lambda token: self._recovery_preflights[token]["expires_at"])[:overflow]
            for token in oldest:
                self._recovery_preflights.pop(token, None)
        return bool(expired or overflow > 0)

    async def _refresh_pending_unlocked(self) -> None:
        pending = [
            (request_id, record)
            for request_id, record in self._records_by_request.items()
            if record.receipt.get("status") in {"pending", "partial"}
        ]
        if not pending:
            return
        try:
            snapshot = await self._snapshot_unlocked()
        except ClimateTabletUnavailable:
            return
        now = self._safe_now()
        changed = False
        for request_id, record in pending:
            if record.request.reliability_profile == "climate_reliability_v1":
                ledger = record.dispatch_ledger
                if not isinstance(ledger, Mapping) or ledger.get("state") != "accepted_unverified":
                    continue
                receipt = _confirmed_reliable_after_read_back(
                    record.receipt, record.request, snapshot, ledger,
                    self._last_reliability_metadata, now,
                )
                if receipt != record.receipt:
                    baseline = _metadata_from_reliable_dispatch_ledger(ledger)
                    dispatched_at = ledger.get("dispatched_at")
                    self._records_by_request[request_id] = _StoredOperation(
                        record.fingerprint, record.request, receipt,
                        _reliable_dispatch_ledger(
                            receipt,
                            (
                                "confirmed" if receipt.get("status") == "confirmed"
                                else "terminal_mixed" if receipt.get("final") is True
                                else "accepted_unverified"
                            ),
                            dispatched_at=dispatched_at,
                            metadata=baseline,
                        ),
                    )
                    changed = True
                continue
            if not _request_matches_snapshot(record.request, snapshot):
                continue
            receipt = _confirmed_after_read_back(record.receipt, snapshot, now)
            self._records_by_request[request_id] = _StoredOperation(
                record.fingerprint,
                record.request,
                receipt,
            )
            changed = True
        if changed:
            await self._async_save()

    def _prune_oldest_final(self) -> None:
        active_intent_origins = {
            (intent.get("origin_request_id"), intent.get("request_fingerprint"))
            for intent in self._desired_intents.values()
            if isinstance(intent, Mapping)
        }
        completed = [
            (record.receipt["updated_at"], request_id, record)
            for request_id, record in self._records_by_request.items()
            if record.receipt.get("final") is True
            and (request_id, record.fingerprint) not in active_intent_origins
        ]
        if not completed:
            return
        _, request_id, record = min(completed)
        self._records_by_request.pop(request_id, None)
        self._request_by_operation.pop(record.receipt["operation_id"], None)
        if record.request.reliability_profile == "climate_reliability_v1":
            self._reliable_scope_binding_cleanup.add(request_id)
            self._legacy_home_execution_facts.pop(record.request.correlation_id, None)

    def _prune_oldest_final_recovery(self) -> None:
        completed = [
            (item["receipt"].get("updated_at", 0), request_id, item)
            for request_id, item in self._recoveries_by_request.items()
            if isinstance(item.get("receipt"), Mapping)
            and item["receipt"].get("final") is True
            and not _recovery_unresolved_device_ids(item.get("ledger", {}))
        ]
        if not completed:
            return
        _, request_id, item = min(completed)
        self._recoveries_by_request.pop(request_id, None)
        receipt = item["receipt"]
        if isinstance(receipt, Mapping):
            operation_id = receipt.get("operation_id")
            if isinstance(operation_id, str):
                self._recovery_by_operation.pop(operation_id, None)

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise ClimateTabletUnavailable("climate operation clock is invalid")
        return value

    def _new_recovery_operation_id(self) -> str:
        operation_id = self._operation_id_factory()
        if (not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None
                or operation_id in self._request_by_operation or operation_id in self._recovery_by_operation):
            # A test or hostile factory may repeat an ID. Never overwrite a
            # durable receipt in that case.
            operation_id = secrets.token_hex(16)
        return operation_id


def _project_device(
    device: object,
    last_confirmed: Mapping[str, object] | None,
) -> dict[str, object]:
    if not isinstance(device, Mapping):
        raise ClimateTabletUnavailable("climate device projection is invalid")
    mode = (
        device.get("mode")
        if device.get("mode") in {"automatic", "manual", "unknown"}
        else "unknown"
    )
    mode_name = {
        "automatic": "Автоматический режим",
        "manual": "Ручной режим",
        "unknown": "Режим неизвестен",
    }[mode]
    projected = {
        "id": device.get("id"),
        "name": device.get("name"),
        "kind": device.get("kind"),
        "control_scope": (
            device.get("control_scope")
            if device.get("control_scope") in {"observed", "canary", "managed"}
            else "observed"
        ),
        "available": device.get("available") is True,
        "state": device.get("state"),
        "mode": mode,
        "mode_name": mode_name,
        "reported_target_temperature": device.get("reported_target_temperature", device.get("target_temperature")),
        "reported_target_humidity": device.get("reported_target_humidity", device.get("target_humidity")),
        "control": _project_device_control(device.get("control")),
        "cooldown": _project_cooldown(device.get("cooldown")),
        "last_confirmed_operation": (
            {
                "operation_id": last_confirmed["operation_id"],
                "action": last_confirmed["action"],
                "updated_at": last_confirmed["updated_at"],
            }
            if last_confirmed is not None
            else None
        ),
    }
    deviation_guard = device.get("deviation_guard")
    if isinstance(deviation_guard, Mapping):
        projected["deviation_guard"] = dict(deviation_guard)
    return projected


def _project_device_control(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {
            "enabled": False,
            "allowed_actions": [],
            "blocked_reasons": ["action_unsupported"],
        }
    allowed = value.get("allowed_actions")
    actions = [
        action for action in allowed if action == "set_device_mode"
    ] if isinstance(allowed, list) else []
    reasons = value.get("blocked_reasons")
    blocked = [
        reason for reason in reasons if isinstance(reason, str)
    ] if isinstance(reasons, list) else []
    enabled = value.get("enabled") is True and bool(actions)
    return {
        "enabled": enabled,
        "allowed_actions": actions if enabled else [],
        "blocked_reasons": [] if enabled else (blocked or ["action_unsupported"]),
    }


def _project_cooldown(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or value.get("active") is not True:
        return None
    remaining = value.get("remaining_seconds")
    reason = value.get("reason")
    if (
        type(remaining) is not int
        or not 1 <= remaining <= 86400
        or reason not in {"minimum_runtime", "minimum_idle", "rate_limit"}
    ):
        return None
    return {
        "active": True,
        "remaining_seconds": remaining,
        "reason": reason,
    }


def _public_range(
    value: object,
    *,
    minimum: int | float,
    maximum: int | float,
    step: int | float,
) -> dict[str, int | float]:
    if not isinstance(value, Mapping):
        return {"minimum": minimum, "maximum": maximum, "step": step}
    candidate_minimum = value.get("minimum")
    candidate_maximum = value.get("maximum")
    candidate_step = value.get("step")
    if (
        isinstance(candidate_minimum, (int, float))
        and not isinstance(candidate_minimum, bool)
        and isinstance(candidate_maximum, (int, float))
        and not isinstance(candidate_maximum, bool)
        and isinstance(candidate_step, (int, float))
        and not isinstance(candidate_step, bool)
        and candidate_minimum <= candidate_maximum
        and candidate_step > 0
    ):
        return {
            "minimum": candidate_minimum,
            "maximum": candidate_maximum,
            "step": candidate_step,
        }
    return {"minimum": minimum, "maximum": maximum, "step": step}


def _disabled_snapshot(
    generated_at: int | None,
    active_operations: tuple[dict[str, object], ...] = (),
) -> dict[str, object]:
    timestamp = generated_at if type(generated_at) is int and generated_at >= 0 else 0
    return {
        "contract": {"name": CLIMATE_RUNTIME_CONTRACT_NAME, "version": 1},
        "generated_at": timestamp,
        "state_revision": 0,
        "phase": "disabled",
        "authority": "none",
        "fresh": False,
        "commands_enabled": False,
        "blocked_reasons": ["climate_disabled"],
        "home_control": {
            "enabled": False,
            "allowed_actions": [],
            "blocked_reasons": ["climate_disabled"],
        },
        "rooms": [],
        "active_operations": [dict(item) for item in active_operations],
    }


def _aggregate_block_reasons(
    *, fresh: bool, reconciliation_matches: bool, pending: bool
) -> list[str]:
    reasons: list[str] = []
    if not fresh:
        reasons.append("state_stale")
    if not reconciliation_matches:
        reasons.append("registry_mismatch")
    if pending:
        reasons.append("operation_pending")
    if not reasons:
        reasons.append("action_unsupported")
    return reasons


def _require_action_allowed(
    snapshot: Mapping[str, object], request: ClimateTabletActionRequest
) -> None:
    if request.action not in _SUPPORTED_ACTIONS:
        raise ClimateTabletViolation(
            "climate action is not available", code="climate_action_unsupported"
        )
    if request.action in {"set_home_targets", "synchronize_home"}:
        control = snapshot.get("home_control")
    else:
        rooms = snapshot.get("rooms")
        room = next(
            (
                item
                for item in rooms if isinstance(item, Mapping) and item.get("id") == request.room_id
            ),
            None,
        ) if isinstance(rooms, list) else None
        if request.action == "set_device_mode" and isinstance(room, Mapping):
            devices = room.get("devices")
            device = next(
                (
                    item for item in devices
                    if isinstance(item, Mapping)
                    and item.get("id") == request.parameters.get("device_id")
                ),
                None,
            ) if isinstance(devices, list) else None
            control = device.get("control") if isinstance(device, Mapping) else None
        else:
            control = room.get("control") if isinstance(room, Mapping) else None
    allowed = control.get("allowed_actions") if isinstance(control, Mapping) else None
    if not isinstance(allowed, list) or request.action not in allowed:
        phase = snapshot.get("phase")
        if phase == "disabled":
            reason = "climate_disabled"
        elif phase == "shadow":
            reason = "climate_shadow_only"
        elif snapshot.get("fresh") is False:
            reason = "climate_state_stale"
        else:
            reason = "climate_authority_not_ready"
        raise ClimateTabletViolation("climate action is not currently allowed", code=reason)


def _require_existing_action_scope(
    snapshot: Mapping[str, object], request: ClimateTabletActionRequest
) -> None:
    """Reject an invented durable intent before it consumes a revision."""

    if request.action not in _SUPPORTED_ROOM_ACTIONS:
        return
    rooms = snapshot.get("rooms")
    room = next(
        (
            item for item in rooms
            if isinstance(item, Mapping) and item.get("id") == request.room_id
        ),
        None,
    ) if isinstance(rooms, list) else None
    if not isinstance(room, Mapping):
        raise ClimateTabletViolation(
            "climate room is not available", code="climate_action_unsupported"
        )
    if request.action != "set_device_mode":
        if request.reliability_profile is None:
            return
        if not _resolved_scope(snapshot, request).get("device_ids"):
            raise ClimateTabletViolation(
                "climate action has no compatible device", code="climate_action_unsupported"
            )
        return
    device_id = request.parameters.get("device_id")
    devices = room.get("devices")
    selected = next(
        (
            device for device in devices
            if isinstance(device, Mapping) and device.get("id") == device_id
        ),
        None,
    ) if isinstance(devices, list) else None
    if not isinstance(selected, Mapping) or (
        request.reliability_profile is not None
        and not _device_supports_action(selected, request)
    ):
        raise ClimateTabletViolation(
            "climate device is not available", code="climate_action_unsupported"
        )


def _pending_receipt(
    request: ClimateTabletActionRequest,
    operation_id: str,
    now: int,
    *,
    snapshot: Mapping[str, object] | None = None,
    resulting_control_revision: int | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "contract": {"name": CLIMATE_OPERATION_CONTRACT_NAME, "version": 1},
        "correlation_id": request.correlation_id,
        "operation_id": operation_id,
        "request_id": request.request_id,
        "action": request.action,
        "room_id": request.room_id,
        "expected_state_revision": request.expected_state_revision,
        "resulting_state_revision": None,
        "status": "pending",
        "accepted": True,
        "confirmed": False,
        "final": False,
        "duplicate": False,
        "reason": "none",
        "message": "Команда принята и ожидает подтверждения состояния.",
        "read_back": {
            "attempted": False,
            "matched": None,
            "observed_at": None,
            "evidence": {},
        },
        "created_at": now,
        "updated_at": now,
        "expires_at": now + CLIMATE_OPERATION_TTL_MS,
    }
    if request.reliability_profile is not None:
        return _reliable_receipt(
            receipt,
            request,
            snapshot or {},
            resulting_control_revision,
        )
    return receipt


def _accepted_unverified_reliable_receipt(
    request: ClimateTabletActionRequest,
    operation_id: str,
    *,
    created_at: int,
    updated_at: int,
    snapshot: Mapping[str, object],
    resulting_control_revision: int | None,
    reliability_metadata: Mapping[tuple[str, str], Mapping[str, object]],
    dispatched_at: int | None,
    pre_dispatch_metadata: Mapping[tuple[str, str], Mapping[str, object]],
) -> dict[str, object]:
    """Project an irreversible physical attempt without inventing success."""

    receipt = _pending_receipt(
        request, operation_id, created_at,
        snapshot=snapshot, resulting_control_revision=resulting_control_revision,
    )
    receipt.update({
        "status": "partial",
        "accepted": True,
        "confirmed": False,
        "final": False,
        "reason": "read_back_mismatch",
        "message": "Команда могла быть передана, требуется подтверждение состояния устройства.",
        "updated_at": updated_at,
        "expires_at": max(created_at + CLIMATE_OPERATION_TTL_MS, updated_at),
        "read_back": {
            "attempted": True,
            "matched": None,
            "observed_at": None,
            "evidence": {},
        },
    })
    return _reliable_receipt(
        receipt, request, snapshot, resulting_control_revision,
        reliability_metadata=reliability_metadata,
        dispatched_at=dispatched_at,
        pre_dispatch_metadata=pre_dispatch_metadata,
    )


def _request_payload(request: ClimateTabletActionRequest) -> dict[str, object]:
    payload = {
        "contract": {"name": CLIMATE_ACTION_CONTRACT_NAME, "version": 1},
        "correlation_id": request.correlation_id,
        "request_id": request.request_id,
        "expected_state_revision": request.expected_state_revision,
        "action": request.action,
        "room_id": request.room_id,
        "parameters": dict(request.parameters),
    }
    if request.reliability_profile is not None:
        payload["reliability_profile"] = request.reliability_profile
        payload["expected_control_revision"] = request.expected_control_revision
    return payload


def _receipt_matches_request(
    receipt: Mapping[str, object],
    request: ClimateTabletActionRequest,
) -> bool:
    basic = (
        receipt.get("request_id") == request.request_id
        and receipt.get("action") == request.action
        and receipt.get("room_id") == request.room_id
        and receipt.get("expected_state_revision")
        == request.expected_state_revision
    )
    if not basic:
        return False
    if request.reliability_profile is None:
        # A legacy record may migrate only when its receipt belongs to the
        # parsed request trace.  Otherwise a valid old digest could attach a
        # different request's receipt before the v6 checkpoint signs it.
        return (
            "action_snapshot" not in receipt
            and receipt.get("correlation_id") == request.correlation_id
        )
    action_snapshot = receipt.get("action_snapshot")
    intent = receipt.get("intent")
    if not isinstance(action_snapshot, Mapping) or not isinstance(intent, Mapping):
        return False
    expected_snapshot = {
        "contract": {"name": CLIMATE_ACTION_CONTRACT_NAME, "version": 1},
        "reliability_profile": "climate_reliability_v1",
        "request_id": request.request_id,
        "correlation_id": request.correlation_id,
        "request_fingerprint": request.fingerprint,
        "action": request.action,
        "room_id": request.room_id,
        "expected_state_revision": request.expected_state_revision,
        "expected_control_revision": request.expected_control_revision,
        "parameters": dict(request.parameters),
    }
    if any(action_snapshot.get(key) != value for key, value in expected_snapshot.items()):
        return False
    scope = action_snapshot.get("resolved_scope")
    resulting = receipt.get("resulting_control_revision")
    return (
        receipt.get("correlation_id") == request.correlation_id
        and receipt.get("request_fingerprint") == request.fingerprint
        and receipt.get("action_parameters") == dict(request.parameters)
        and receipt.get("expected_control_revision") == request.expected_control_revision
        and receipt.get("confirmation_window_ms") == 30_000
        and isinstance(scope, Mapping)
        and _valid_frozen_scope(scope, request)
        and intent.get("request_fingerprint") == request.fingerprint
        and intent.get("resolved_scope") == scope
        and intent.get("scope_fingerprint") == _canonical_fingerprint(scope)
        and (
            (receipt.get("accepted") is True
             and resulting == request.expected_control_revision + 1
             and intent.get("control_revision") == resulting
             and intent.get("scope_revision") == resulting)
            or (receipt.get("accepted") is False and resulting is None)
        )
        and _valid_reliable_receipt_proof(receipt, request, scope)
    )


def _valid_reliable_receipt_proof(
    receipt: Mapping[str, object], request: ClimateTabletActionRequest,
    scope: Mapping[str, object],
) -> bool:
    """Validate frozen reliable leaf proof before a durable replay is exposed."""

    if not _valid_frozen_scope(scope, request):
        return False
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    scope_rows = scope.get("devices_by_room") if isinstance(scope, Mapping) else None
    if not isinstance(rooms, Mapping) or not isinstance(scope_rows, list):
        return False
    expected_rooms = {
        row.get("room_id"): row.get("device_ids")
        for row in scope_rows
        if isinstance(row, Mapping)
        and isinstance(row.get("room_id"), str)
        and isinstance(row.get("device_ids"), list)
    }
    if set(rooms) != set(expected_rooms):
        return False
    unfinished = 0
    all_confirmed = True
    for room_id, device_ids in expected_rooms.items():
        room = rooms.get(room_id)
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping) or set(devices) != set(device_ids):
            return False
        for device_id, leaf in devices.items():
            if not isinstance(leaf, Mapping):
                return False
            status = leaf.get("status")
            execution = leaf.get("execution_state")
            if status == "confirmed":
                if execution == "applied":
                    expected_counts = (1, 1)
                elif execution == "already_in_sync":
                    expected_counts = (0, 0)
                else:
                    return False
                if not _strict_leaf_counts(leaf, *expected_counts):
                    return False
                evidence = leaf.get("evidence")
                action = evidence.get("action") if isinstance(evidence, Mapping) else None
                actual = evidence.get("observed_actual") if isinstance(evidence, Mapping) else None
                if (
                    not isinstance(action, Mapping)
                    or set(action) != {"request_fingerprint", "action", "parameters"}
                    or action.get("request_fingerprint") != request.fingerprint
                    or action.get("action") != request.action
                    or action.get("parameters") != dict(request.parameters)
                    or evidence.get("desired_target_temperature")
                    != request.parameters.get("target_temperature")
                    or evidence.get("desired_target_humidity")
                    != request.parameters.get("target_humidity")
                    or not isinstance(actual, Mapping)
                    or actual.get("desired_target_temperature")
                    != request.parameters.get("target_temperature")
                    or actual.get("desired_target_humidity")
                    != request.parameters.get("target_humidity")
                    or type(evidence.get("observed_at")) is not int
                    or not 1 <= evidence["observed_at"] <= 9_007_199_254_740_991
                    # A physical application needs a post-request observation.
                    # For already_in_sync, the authoritative current state may
                    # legitimately predate the request that merely recorded it.
                    or (
                        execution == "applied"
                        and evidence["observed_at"] <= receipt.get("created_at", 0)
                    )
                    or evidence.get("fresh") is not True
                    or not _reliable_evidence_matches_request(evidence, request)
                ):
                    return False
            elif status == "pending":
                if execution not in {"pending_dispatch", "accepted_unverified"}:
                    return False
                if not _strict_leaf_counts(
                    leaf, 0 if execution == "pending_dispatch" else 1,
                    0 if execution == "pending_dispatch" else 1,
                ):
                    return False
                unfinished += 1
                all_confirmed = False
            elif status in {"failed", "not_attempted", "deferred"}:
                expected = {
                    "dispatched_not_accepted": (1, 0),
                    "accepted_timeout": (1, 1),
                    "blocked_before_dispatch": (0, 0),
                    "superseded_by_newer_intent": (0, 0),
                }.get(execution)
                if expected is not None and not _strict_leaf_counts(leaf, *expected):
                    return False
                all_confirmed = False
            else:
                return False
    if receipt.get("unfinished_device_count") != unfinished:
        return False
    if receipt.get("status") == "confirmed":
        read_back = receipt.get("read_back")
        return (
            all_confirmed
            and isinstance(read_back, Mapping)
            and read_back.get("attempted") is True
            and read_back.get("matched") is True
        )
    return not all_confirmed


def _strict_leaf_counts(
    leaf: Mapping[str, object], command_count: int, accepted_count: int,
) -> bool:
    """Accept only real integer command counters, never bool or float aliases."""

    commands = leaf.get("command_count", 0)
    accepted = leaf.get("accepted_count", 0)
    return (
        type(commands) is int
        and type(accepted) is int
        and (commands, accepted) == (command_count, accepted_count)
    )


def _valid_frozen_scope(
    scope: Mapping[str, object], request: ClimateTabletActionRequest,
) -> bool:
    """Require the durable device scope to be canonical and request-bound."""

    if set(scope) != {"room_ids", "device_ids", "devices_by_room"}:
        return False
    rows = scope.get("devices_by_room")
    room_ids = scope.get("room_ids")
    device_ids = scope.get("device_ids")
    if (
        not isinstance(rows, list)
        or not isinstance(room_ids, list)
        or not isinstance(device_ids, list)
        or not 1 <= len(rows) <= 128
        or not 1 <= len(room_ids) <= 128
        or not 1 <= len(device_ids) <= 4096
    ):
        return False
    canonical_rooms: list[str] = []
    canonical_devices: list[str] = []
    seen_rooms: set[str] = set()
    seen_devices: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"room_id", "device_ids"}:
            return False
        room_id = row.get("room_id")
        row_devices = row.get("device_ids")
        if (
            not isinstance(room_id, str)
            or _STABLE_ID.fullmatch(room_id) is None
            or room_id in seen_rooms
            or not isinstance(row_devices, list)
            or not 1 <= len(row_devices) <= 32
            or any(
                type(device_id) is not str
                or _STABLE_ID.fullmatch(device_id) is None
                for device_id in row_devices
            )
            or row_devices != sorted(set(row_devices))
            or any(device_id in seen_devices for device_id in row_devices)
        ):
            return False
        canonical_rooms.append(room_id)
        canonical_devices.extend(row_devices)
        seen_rooms.add(room_id)
        seen_devices.update(row_devices)
    if (
        not canonical_rooms
        or canonical_rooms != sorted(canonical_rooms)
        or room_ids != canonical_rooms
        or device_ids != canonical_devices
    ):
        return False
    if request.room_id is not None:
        if canonical_rooms != [request.room_id]:
            return False
    elif request.action not in {"set_home_targets", "synchronize_home"}:
        return False
    if request.action == "set_device_mode":
        return canonical_devices == [request.parameters.get("device_id")]
    return True


def _reliable_scope_binding(
    request: ClimateTabletActionRequest,
    receipt: Mapping[str, object],
    integrity_key: bytes | None,
    metadata: Mapping[tuple[str, str], Mapping[str, object]],
    dispatch_ledger: Mapping[str, object] | None,
) -> dict[str, object]:
    """Store the server-authored reliable scope outside the mutable receipt."""

    action_snapshot = receipt.get("action_snapshot")
    scope = (
        action_snapshot.get("resolved_scope")
        if isinstance(action_snapshot, Mapping)
        else None
    )
    operation_id = receipt.get("operation_id")
    if (
        not isinstance(scope, Mapping)
        or not _valid_frozen_scope(scope, request)
        or not isinstance(operation_id, str)
        or _OPERATION_ID.fullmatch(operation_id) is None
        or not _valid_integrity_key(integrity_key)
    ):
        raise ClimateTabletUnavailable("climate reliable scope is invalid")
    frozen_scope = json.loads(json.dumps(scope, ensure_ascii=False))
    sources: dict[str, dict[str, int | None]] = {}
    for row in frozen_scope["devices_by_room"]:
        room_id = row["room_id"]
        room_sources: dict[str, int | None] = {}
        for device_id in row["device_ids"]:
            value = metadata.get((room_id, device_id), {}).get("source_observed_at")
            room_sources[device_id] = (
                value
                if type(value) is int and 1 <= value <= 9_007_199_254_740_991
                else None
            )
        sources[room_id] = room_sources
    binding = {
        "request_id": request.request_id,
        "request_fingerprint": request.fingerprint,
        "operation_id": operation_id,
        "resolved_scope": frozen_scope,
        "scope_fingerprint": _canonical_fingerprint(frozen_scope),
        "source_observed_at": sources,
    }
    signed = {
        **binding,
        "integrity_tag": _reliable_scope_integrity_tag(
            integrity_key, binding
        ),
    }
    return _binding_with_checkpoint(signed, receipt, dispatch_ledger, integrity_key)


def _valid_reliable_scope_binding(
    binding: object,
    request: ClimateTabletActionRequest,
    receipt: Mapping[str, object],
    dispatch_ledger: object,
    integrity_key: bytes | None,
) -> bool:
    """Bind a durable reliable receipt to its separate server-side scope."""

    if not isinstance(binding, Mapping) or set(binding) != {
        "request_id", "request_fingerprint", "operation_id",
        "resolved_scope", "scope_fingerprint", "source_observed_at", "integrity_tag",
        "operation_checkpoints",
    }:
        return False
    action_snapshot = receipt.get("action_snapshot")
    scope = (
        action_snapshot.get("resolved_scope")
        if isinstance(action_snapshot, Mapping)
        else None
    )
    binding_scope = binding.get("resolved_scope")
    unsigned = {
        key: value for key, value in binding.items()
        if key not in {"integrity_tag", "operation_checkpoints"}
    }
    return (
        binding.get("request_id") == request.request_id
        and binding.get("request_fingerprint") == request.fingerprint
        and binding.get("operation_id") == receipt.get("operation_id")
        and isinstance(binding_scope, Mapping)
        and _valid_frozen_scope(binding_scope, request)
        and binding.get("scope_fingerprint") == _canonical_fingerprint(binding_scope)
        and scope == binding_scope
        and _valid_integrity_key(integrity_key)
        and isinstance(binding.get("integrity_tag"), str)
        and _valid_reliable_binding_sources(binding.get("source_observed_at"), binding_scope)
        and _reliable_receipt_matches_binding_sources(receipt, binding.get("source_observed_at"))
        and _valid_operation_checkpoint(
            binding.get("operation_checkpoints"), receipt, dispatch_ledger,
            integrity_key, unsigned,
        )
        and _reliable_scope_integrity_matches(integrity_key, binding["integrity_tag"], unsigned)
    )


def _checkpoint_payload(
    binding: Mapping[str, object], receipt: Mapping[str, object], ledger: object,
) -> dict[str, object]:
    return {"binding": dict(binding), "receipt": dict(receipt), "dispatch_ledger": ledger}


def _tablet_state_payload(payload: Mapping[str, object]) -> dict[str, object]:
    keys = {
        "version", "records", "recoveries",
        "desired_intents", "recovery_preflights",
    }
    return {key: payload.get(key) for key in sorted(keys)}


def _tablet_state_with_checkpoint(
    previous: object, payload: Mapping[str, object], integrity_key: bytes | None,
) -> dict[str, object]:
    if not _valid_integrity_key(integrity_key):
        raise ClimateTabletUnavailable("climate tablet state key is invalid")
    state = _tablet_state_payload(payload)
    fingerprint = _canonical_fingerprint(state)
    checkpoint = {
        "fingerprint": fingerprint,
        "integrity_tag": _reliable_scope_integrity_tag(integrity_key, state),
    }
    old = previous.get("checkpoints", []) if isinstance(previous, Mapping) else []
    retained = [item for item in old if isinstance(item, Mapping) and item.get("fingerprint") != fingerprint]
    return {"checkpoints": [*retained[-1:], checkpoint]}


def _tablet_state_with_only_current_checkpoint(
    previous: object, payload: Mapping[str, object], integrity_key: bytes | None,
) -> dict[str, object]:
    updated = _tablet_state_with_checkpoint(previous, payload, integrity_key)
    return {"checkpoints": updated["checkpoints"][-1:]}


def _valid_tablet_state_checkpoint(
    stored: object, payload: Mapping[str, object], integrity_key: bytes | None,
) -> bool:
    if not isinstance(stored, Mapping) or set(stored) != {"checkpoints"} or not _valid_integrity_key(integrity_key):
        return False
    checkpoints = stored.get("checkpoints")
    if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 2:
        return False
    state = _tablet_state_payload(payload)
    fingerprint = _canonical_fingerprint(state)
    return any(
        isinstance(item, Mapping)
        and set(item) == {"fingerprint", "integrity_tag"}
        and item.get("fingerprint") == fingerprint
        and isinstance(item.get("integrity_tag"), str)
        and _reliable_scope_integrity_matches(integrity_key, item["integrity_tag"], state)
        for item in checkpoints
    )


def _binding_with_checkpoint(
    binding: Mapping[str, object], receipt: Mapping[str, object], ledger: object,
    integrity_key: bytes | None,
) -> dict[str, object]:
    if not _valid_integrity_key(integrity_key):
        raise ClimateTabletUnavailable("climate reliable scope is invalid")
    base = {key: value for key, value in binding.items() if key != "operation_checkpoints"}
    unsigned = {key: value for key, value in base.items() if key != "integrity_tag"}
    payload = _checkpoint_payload(unsigned, receipt, ledger)
    fingerprint = _canonical_fingerprint(payload)
    checkpoint = {
        "fingerprint": fingerprint,
        "integrity_tag": _reliable_scope_integrity_tag(integrity_key, payload),
    }
    previous = binding.get("operation_checkpoints", [])
    retained = [item for item in previous if isinstance(item, Mapping) and item.get("fingerprint") != fingerprint]
    return {**base, "operation_checkpoints": [*retained[-1:], checkpoint]}


def _binding_with_only_current_checkpoint(
    binding: Mapping[str, object], receipt: Mapping[str, object], ledger: object,
    integrity_key: bytes | None,
) -> dict[str, object]:
    updated = _binding_with_checkpoint(binding, receipt, ledger, integrity_key)
    return {**updated, "operation_checkpoints": updated["operation_checkpoints"][-1:]}


def _valid_operation_checkpoint(
    checkpoints: object, receipt: Mapping[str, object], ledger: object,
    integrity_key: bytes, binding: Mapping[str, object],
) -> bool:
    if not isinstance(checkpoints, list) or not 1 <= len(checkpoints) <= 2:
        return False
    payload = _checkpoint_payload(binding, receipt, ledger)
    fingerprint = _canonical_fingerprint(payload)
    return any(
        isinstance(item, Mapping)
        and set(item) == {"fingerprint", "integrity_tag"}
        and item.get("fingerprint") == fingerprint
        and isinstance(item.get("integrity_tag"), str)
        and _reliable_scope_integrity_matches(integrity_key, item["integrity_tag"], payload)
        for item in checkpoints
    )


def _valid_integrity_key(integrity_key: object) -> bool:
    return isinstance(integrity_key, bytes) or isinstance(integrity_key, ClimateLedgerKeyring)


def _reliable_scope_integrity_tag(
    integrity_key: bytes | ClimateLedgerKeyring,
    binding: Mapping[str, object],
) -> str:
    """Authenticate immutable scope and proof outside climate operation stores."""

    payload = json.dumps(
        binding,
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    key = integrity_key.active_key if isinstance(integrity_key, ClimateLedgerKeyring) else integrity_key
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _reliable_scope_integrity_matches(
    integrity_key: bytes | ClimateLedgerKeyring, tag: object, binding: Mapping[str, object],
) -> bool:
    if not isinstance(tag, str):
        return False
    payload = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    keys = integrity_key.keys.values() if isinstance(integrity_key, ClimateLedgerKeyring) else (integrity_key,)
    return any(hmac.compare_digest(tag, hmac.new(key, payload, hashlib.sha256).hexdigest()) for key in keys)


def _valid_reliable_binding_sources(sources: object, scope: Mapping[str, object]) -> bool:
    rows = scope.get("devices_by_room")
    if not isinstance(sources, Mapping) or not isinstance(rows, list):
        return False
    expected = {
        row["room_id"]: row["device_ids"]
        for row in rows
        if isinstance(row, Mapping)
    }
    if set(sources) != set(expected):
        return False
    return all(
        isinstance(sources.get(room_id), Mapping)
        and set(sources[room_id]) == set(device_ids)
        and all(
            value is None
            or type(value) is int and 1 <= value <= 9_007_199_254_740_991
            for value in sources[room_id].values()
        )
        for room_id, device_ids in expected.items()
    )


def _reliable_receipt_matches_binding_sources(
    receipt: Mapping[str, object], sources: object,
) -> bool:
    if not isinstance(sources, Mapping):
        return False
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping):
        return False
    for room_id, room in rooms.items():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        room_sources = sources.get(room_id)
        if not isinstance(devices, Mapping) or not isinstance(room_sources, Mapping):
            return False
        for device_id, leaf in devices.items():
            source = room_sources.get(device_id)
            evidence = leaf.get("evidence") if isinstance(leaf, Mapping) else None
            execution = leaf.get("execution_state") if isinstance(leaf, Mapping) else None
            if execution == "already_in_sync":
                if not isinstance(evidence, Mapping) or evidence.get("observed_at") != source:
                    return False
            elif execution == "applied":
                observed_at = evidence.get("observed_at") if isinstance(evidence, Mapping) else None
                if type(observed_at) is not int or type(source) is not int or observed_at <= source:
                    return False
    return True


def _reliable_dispatch_ledger(
    receipt: Mapping[str, object],
    state: str,
    *,
    dispatched_at: int | None = None,
    metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Keep the ordinary physical boundary outside the public receipt."""

    scope = receipt.get("action_snapshot")
    scope = scope.get("resolved_scope") if isinstance(scope, Mapping) else None
    rows = scope.get("devices_by_room") if isinstance(scope, Mapping) else None
    sources: dict[str, dict[str, int]] = {}
    if state not in {
        "pending_dispatch", "pending_expired", "superseded_pre_dispatch",
        "already_in_sync", "blocked_before_dispatch",
    }:
        if type(dispatched_at) is not int:
            raise ClimateTabletUnavailable("climate dispatch boundary is invalid")
        if not isinstance(rows, list) or not isinstance(metadata, Mapping):
            raise ClimateTabletUnavailable("climate dispatch proof is unavailable")
        for row in rows:
            room_id = row.get("room_id") if isinstance(row, Mapping) else None
            device_ids = row.get("device_ids") if isinstance(row, Mapping) else None
            if not isinstance(room_id, str) or not isinstance(device_ids, list):
                raise ClimateTabletUnavailable("climate dispatch scope is invalid")
            room_sources: dict[str, int] = {}
            for device_id in device_ids:
                source = metadata.get((room_id, device_id), {})
                observed_at = (
                    source.get("source_observed_at")
                    if isinstance(source, Mapping) else None
                )
                if type(device_id) is not str or type(observed_at) is not int:
                    raise ClimateTabletUnavailable("climate dispatch proof is unavailable")
                room_sources[device_id] = observed_at
            sources[room_id] = room_sources
    if state == "already_in_sync":
        dispatched_at = None
    return {
        "state": state,
        "dispatched_at": dispatched_at,
        "pre_dispatch_sources": sources,
    }


def _valid_reliable_dispatch_ledger(
    ledger: object,
    receipt: Mapping[str, object],
    request: ClimateTabletActionRequest,
) -> bool:
    if not isinstance(ledger, Mapping) or set(ledger) != {
        "state", "dispatched_at", "pre_dispatch_sources"
    }:
        return False
    state = ledger.get("state")
    dispatched_at = ledger.get("dispatched_at")
    sources = ledger.get("pre_dispatch_sources")
    snapshot = receipt.get("action_snapshot")
    scope = snapshot.get("resolved_scope") if isinstance(snapshot, Mapping) else None
    rows = scope.get("devices_by_room") if isinstance(scope, Mapping) else None
    leaves = _reliable_receipt_leaves(receipt)
    if (
        not isinstance(scope, Mapping)
        or not _valid_frozen_scope(scope, request)
        or not _reliable_receipt_summary_matches_leaves(receipt, leaves)
    ):
        return False
    if state == "pending_dispatch":
        return (
            dispatched_at is None
            and sources == {}
            and receipt.get("status") == "pending"
            and receipt.get("accepted") is True
            and receipt.get("confirmed") is False
            and receipt.get("final") is False
            and _reliable_intent_has_status(receipt, "saved_pending_confirmation")
            and isinstance(leaves, list)
            and receipt.get("read_back", {}).get("attempted") is False
            and all(
                leaf.get("status") == "pending"
                and leaf.get("execution_state") == "pending_dispatch"
                and _strict_leaf_counts(leaf, 0, 0)
                for leaf in leaves
            )
        )
    if state in {"pending_expired", "blocked_before_dispatch"}:
        if (
            state == "blocked_before_dispatch"
            and dispatched_at is None
            and sources == {}
            and receipt.get("status") == "unavailable"
            and receipt.get("accepted") is False
            and receipt.get("confirmed") is False
            and receipt.get("final") is True
            and _reliable_intent_has_status(receipt, "unsaved_unavailable")
            and receipt.get("reason") == "action_unsupported"
            and receipt.get("message_code") == "unavailable"
            and receipt.get("unfinished_device_count") == 0
            and isinstance(leaves, list)
            and all(
                leaf.get("status") == "not_attempted"
                and leaf.get("execution_state") == "blocked_before_dispatch"
                and _strict_leaf_counts(leaf, 0, 0)
                for leaf in leaves
            )
        ):
            return True
        return (
            dispatched_at is None
            and sources == {}
            and receipt.get("status") == "partial"
            and receipt.get("accepted") is True
            and receipt.get("confirmed") is False
            and receipt.get("final") is True
            and _reliable_intent_has_status(
                receipt,
                "saved_blocked_before_dispatch"
                if state == "blocked_before_dispatch"
                else "saved_apply_failed",
            )
            and receipt.get("reason") == "read_back_mismatch"
            and receipt.get("message_code") == "partial"
            and receipt.get("unfinished_device_count") == 0
            and receipt.get("read_back") == {
                "attempted": False, "matched": None,
                "observed_at": None, "evidence": {},
            }
            and isinstance(leaves, list)
            and all(
                leaf.get("status") == "not_attempted"
                and leaf.get("reason") == "configuration_error"
                and leaf.get("execution_state") == "blocked_before_dispatch"
                and _strict_leaf_counts(leaf, 0, 0)
                and leaf.get("message_code") == "configuration_error"
                for leaf in leaves
            )
        )
    if state == "superseded_pre_dispatch":
        return (
            dispatched_at is None
            and sources == {}
            and receipt.get("status") == "partial"
            and receipt.get("accepted") is True
            and receipt.get("confirmed") is False
            and receipt.get("final") is True
            and _reliable_intent_has_status(receipt, "superseded_by_newer_intent")
            and receipt.get("reason") == "operation_pending"
            and receipt.get("message_code") == "partial"
            and receipt.get("unfinished_device_count") == 0
            and receipt.get("read_back") == {
                "attempted": False, "matched": None,
                "observed_at": None, "evidence": {},
            }
            and isinstance(leaves, list)
            and all(
                leaf.get("status") == "not_attempted"
                and leaf.get("reason") == "none"
                and leaf.get("execution_state") == "superseded_by_newer_intent"
                and _strict_leaf_counts(leaf, 0, 0)
                and leaf.get("message_code") == "superseded"
                and leaf.get("message") == "Команда заменена более новой сохранённой целью."
                for leaf in leaves
            )
        )
    if state == "already_in_sync":
        return (
            dispatched_at is None
            and sources == {}
            and receipt.get("status") == "confirmed"
            and receipt.get("accepted") is True
            and receipt.get("confirmed") is True
            and receipt.get("final") is True
            and _reliable_intent_has_status(receipt, "saved_and_applied")
            and isinstance(leaves, list)
            and receipt.get("read_back", {}).get("attempted") is True
            and all(
                leaf.get("status") == "confirmed"
                and leaf.get("execution_state") == "already_in_sync"
                and _strict_leaf_counts(leaf, 0, 0)
                and isinstance(leaf.get("evidence"), Mapping)
                for leaf in leaves
            )
        )
    if state not in {
        "started", "accepted_unverified", "accepted_timeout", "terminal_mixed", "confirmed",
    }:
        return False
    if (
        type(dispatched_at) is not int
        or not 1 <= dispatched_at <= 9_007_199_254_740_991
        or not isinstance(rows, list)
        or not isinstance(sources, Mapping)
    ):
        return False
    expected: dict[str, set[str]] = {}
    for row in rows:
        room_id = row.get("room_id") if isinstance(row, Mapping) else None
        device_ids = row.get("device_ids") if isinstance(row, Mapping) else None
        if not isinstance(room_id, str) or not isinstance(device_ids, list):
            return False
        expected[room_id] = set(device_ids)
    if set(sources) != set(expected):
        return False
    for room_id, device_ids in expected.items():
        values = sources.get(room_id)
        if not isinstance(values, Mapping) or set(values) != device_ids:
            return False
        if any(type(value) is not int or value < 0 for value in values.values()):
            return False
    if state == "started":
        return (
            receipt.get("status") == "pending"
            and receipt.get("accepted") is True
            and receipt.get("confirmed") is False
            and receipt.get("final") is False
            and _reliable_intent_has_status(receipt, "saved_pending_confirmation")
            and isinstance(leaves, list)
            and receipt.get("read_back", {}).get("attempted") is False
            and all(
                leaf.get("status") == "pending"
                and leaf.get("execution_state") == "pending_dispatch"
                and _strict_leaf_counts(leaf, 0, 0)
                for leaf in leaves
            )
        )
    if state == "accepted_unverified":
        outcomes = receipt.get("outcomes")
        room_outcomes = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
        if (
            receipt.get("final") is not False
            or receipt.get("accepted") is not True
            or receipt.get("confirmed") is not False
            or receipt.get("status") not in {"pending", "partial"}
            or receipt.get("read_back", {}).get("attempted") is not True
            or not isinstance(room_outcomes, Mapping)
            or not _reliable_intent_has_status(receipt, "saved_pending_confirmation")
        ):
            return False
        unresolved = 0
        for room_id, device_ids in expected.items():
            room = room_outcomes.get(room_id)
            device_outcomes = room.get("devices") if isinstance(room, Mapping) else None
            if not isinstance(device_outcomes, Mapping) or set(device_outcomes) != device_ids:
                return False
            for device_id in device_ids:
                leaf = device_outcomes.get(device_id)
                if not isinstance(leaf, Mapping):
                    return False
                if (
                    leaf.get("status") == "pending"
                    and leaf.get("execution_state") == "accepted_unverified"
                    and _strict_leaf_counts(leaf, 1, 1)
                ):
                    unresolved += 1
                    continue
                if (
                    leaf.get("status") == "failed"
                    and leaf.get("execution_state") == "dispatched_not_accepted"
                    and _strict_leaf_counts(leaf, 1, 0)
                ):
                    continue
                evidence = leaf.get("evidence") if isinstance(leaf, Mapping) else None
                observed_at = evidence.get("observed_at") if isinstance(evidence, Mapping) else None
                if not (
                    leaf.get("status") == "confirmed"
                    and leaf.get("execution_state") == "applied"
                    and _strict_leaf_counts(leaf, 1, 1)
                    and type(observed_at) is int
                    and dispatched_at < observed_at <= dispatched_at + 30_000
                    and observed_at > sources[room_id][device_id]
                ):
                    return False
        return unresolved > 0
    if state == "accepted_timeout":
        outcomes = receipt.get("outcomes")
        room_outcomes = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
        if (
            receipt.get("final") is not True
            or receipt.get("accepted") is not True
            or receipt.get("confirmed") is not False
            or receipt.get("status") not in {"timed_out", "partial"}
            or receipt.get("read_back", {}).get("attempted") is not True
            or not isinstance(room_outcomes, Mapping)
            or not _reliable_intent_has_status(receipt, "saved_apply_failed")
        ):
            return False
        timed_out = 0
        for room_id, device_ids in expected.items():
            room = room_outcomes.get(room_id)
            device_outcomes = room.get("devices") if isinstance(room, Mapping) else None
            if not isinstance(device_outcomes, Mapping) or set(device_outcomes) != device_ids:
                return False
            for device_id in device_ids:
                leaf = device_outcomes.get(device_id)
                if not isinstance(leaf, Mapping):
                    return False
                if (
                    leaf.get("status") == "failed"
                    and leaf.get("execution_state") == "accepted_timeout"
                    and _strict_leaf_counts(leaf, 1, 1)
                ):
                    timed_out += 1
                    continue
                if (
                    leaf.get("status") == "failed"
                    and leaf.get("execution_state") == "dispatched_not_accepted"
                    and _strict_leaf_counts(leaf, 1, 0)
                ):
                    continue
                evidence = leaf.get("evidence") if isinstance(leaf, Mapping) else None
                observed_at = evidence.get("observed_at") if isinstance(evidence, Mapping) else None
                if not (
                    leaf.get("status") == "confirmed"
                    and leaf.get("execution_state") == "applied"
                    and _strict_leaf_counts(leaf, 1, 1)
                    and type(observed_at) is int
                    and dispatched_at < observed_at <= dispatched_at + 30_000
                    and observed_at > sources[room_id][device_id]
                ):
                    return False
        return timed_out > 0
    if state == "terminal_mixed":
        outcomes = receipt.get("outcomes")
        room_outcomes = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
        if (
            receipt.get("status") != "partial"
            or receipt.get("final") is not True
            or receipt.get("confirmed") is not False
            or not isinstance(room_outcomes, Mapping)
            or not _reliable_intent_has_status(receipt, "saved_apply_failed")
        ):
            return False
        failed = 0
        for room_id, device_ids in expected.items():
            room = room_outcomes.get(room_id)
            device_outcomes = room.get("devices") if isinstance(room, Mapping) else None
            if not isinstance(device_outcomes, Mapping) or set(device_outcomes) != device_ids:
                return False
            for device_id in device_ids:
                leaf = device_outcomes.get(device_id)
                if not isinstance(leaf, Mapping):
                    return False
                if (
                    leaf.get("status") == "failed"
                    and leaf.get("execution_state") == "dispatched_not_accepted"
                    and _strict_leaf_counts(leaf, 1, 0)
                ):
                    failed += 1
                    continue
                evidence = leaf.get("evidence") if isinstance(leaf, Mapping) else None
                observed_at = evidence.get("observed_at") if isinstance(evidence, Mapping) else None
                if not (
                    leaf.get("status") == "confirmed"
                    and leaf.get("execution_state") == "applied"
                    and _strict_leaf_counts(leaf, 1, 1)
                    and type(observed_at) is int
                    and dispatched_at < observed_at <= dispatched_at + 30_000
                    and observed_at > sources[room_id][device_id]
                ):
                    return False
        return failed > 0
    if (
        receipt.get("status") != "confirmed"
        or receipt.get("accepted") is not True
        or receipt.get("confirmed") is not True
        or receipt.get("final") is not True
        or not _reliable_intent_has_status(receipt, "saved_and_applied")
    ):
        return False
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping):
        return False
    for room_id, device_ids in expected.items():
        room = rooms.get(room_id)
        leaves = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(leaves, Mapping) or set(leaves) != device_ids:
            return False
        for device_id in device_ids:
            leaf = leaves.get(device_id)
            evidence = leaf.get("evidence") if isinstance(leaf, Mapping) else None
            observed_at = evidence.get("observed_at") if isinstance(evidence, Mapping) else None
            if (
                isinstance(leaf, Mapping)
                and leaf.get("execution_state") == "already_in_sync"
            ):
                if not (
                    leaf.get("status") == "confirmed"
                    and _strict_leaf_counts(leaf, 0, 0)
                    and type(observed_at) is int
                    and observed_at == sources[room_id][device_id]
                ):
                    return False
                continue
            if (
                not isinstance(leaf, Mapping)
                or leaf.get("status") != "confirmed"
                or not _strict_leaf_counts(leaf, 1, 1)
                or type(observed_at) is not int
                or not dispatched_at < observed_at <= dispatched_at + 30_000
                or observed_at <= sources[room_id][device_id]
            ):
                return False
    return True


def _reliable_receipt_leaves(
    receipt: Mapping[str, object],
) -> list[Mapping[str, object]] | None:
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping):
        return None
    leaves: list[Mapping[str, object]] = []
    for room in rooms.values():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping):
            return None
        for leaf in devices.values():
            if not isinstance(leaf, Mapping):
                return None
            leaves.append(leaf)
    return leaves


def _reliable_receipt_is_already_in_sync(receipt: Mapping[str, object]) -> bool:
    leaves = _reliable_receipt_leaves(receipt)
    return bool(leaves) and all(
        leaf.get("status") == "confirmed"
        and leaf.get("execution_state") == "already_in_sync"
        and _strict_leaf_counts(leaf, 0, 0)
        for leaf in leaves
    )


def _reliable_intent_has_status(
    receipt: Mapping[str, object], expected_status: str,
) -> bool:
    intent = receipt.get("intent")
    return isinstance(intent, Mapping) and intent.get("status") == expected_status


def _reliable_receipt_summary_matches_leaves(
    receipt: Mapping[str, object],
    leaves: list[Mapping[str, object]] | None,
) -> bool:
    """Bind public aggregate proof to the immutable per-device leaves."""

    if not isinstance(leaves, list):
        return False
    read_back = receipt.get("read_back")
    evidence = read_back.get("evidence") if isinstance(read_back, Mapping) else None
    if not isinstance(evidence, Mapping):
        return False
    accepted_count = sum(
        leaf.get("execution_state")
        in {"applied", "accepted_unverified", "accepted_timeout"}
        for leaf in leaves
    )
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping):
        return False
    confirmed_room_count = 0
    for room in rooms.values():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping):
            return False
        room_leaves = [leaf for leaf in devices.values() if isinstance(leaf, Mapping)]
        if len(room_leaves) != len(devices):
            return False
        if room_leaves and all(leaf.get("status") == "confirmed" for leaf in room_leaves):
            confirmed_room_count += 1
    if (
        evidence == {}
        and isinstance(read_back, Mapping)
        and read_back.get("attempted") is False
        and accepted_count == 0
        and confirmed_room_count == 0
    ):
        return True
    return (
        type(evidence.get("accepted_count")) is int
        and type(evidence.get("confirmed_room_count")) is int
        and evidence.get("accepted_count") == accepted_count
        and evidence.get("confirmed_room_count") == confirmed_room_count
    )


def _request_matches_snapshot(
    request: ClimateTabletActionRequest,
    snapshot: Mapping[str, object],
) -> bool:
    if snapshot.get("fresh") is not True:
        return False
    rooms = snapshot.get("rooms")
    if not isinstance(rooms, list) or not rooms:
        return False
    if request.action == "set_home_targets":
        for room in rooms:
            if not isinstance(room, Mapping):
                return False
            if (
                "target_temperature" in request.parameters
                and room.get("target_temperature")
                != request.parameters["target_temperature"]
            ):
                return False
            if (
                "target_humidity" in request.parameters
                and room.get("target_humidity")
                != request.parameters["target_humidity"]
            ):
                return False
        return True
    if request.action == "synchronize_home":
        return all(
            isinstance(room, Mapping)
            and isinstance(room.get("devices"), list)
            and all(
                isinstance(device, Mapping)
                and isinstance(device.get("participation"), Mapping)
                and device["participation"].get("synchronization") == "in_sync"
                for device in room["devices"]
            )
            for room in rooms
        )
    room = next(
        (
            item
            for item in rooms
            if isinstance(item, Mapping) and item.get("id") == request.room_id
        ),
        None,
    )
    if not isinstance(room, Mapping):
        return False
    temporary = room.get("temporary_override")
    if not isinstance(temporary, Mapping):
        return False
    if request.action == "set_room_target":
        return (
            temporary.get("active") is True
            and temporary.get("target_temperature")
            == request.parameters.get("target_temperature")
            and room.get("target_temperature")
            == request.parameters.get("target_temperature")
        )
    if request.action == "set_room_humidity_target":
        return room.get("target_humidity") == request.parameters.get("target_humidity")
    if request.action == "set_room_min_target":
        return room.get("minimum_temperature") == request.parameters.get("minimum_temperature")
    if request.action == "set_room_target_strategy":
        return room.get("target_strategy") == request.parameters.get("target_strategy")
    if request.action == "clear_room_override":
        return temporary.get("active") is False
    if request.action == "set_room_mode":
        return room.get("mode") == request.parameters.get("mode")
    if request.action == "set_device_mode":
        devices = room.get("devices")
        device = next(
            (
                item for item in devices
                if isinstance(item, Mapping)
                and item.get("id") == request.parameters.get("device_id")
            ),
            None,
        ) if isinstance(devices, list) else None
        return isinstance(device, Mapping) and device.get("mode") == request.parameters.get("mode")
    if request.action == "turn_room_off":
        devices = room.get("devices")
        return isinstance(devices, list) and bool(devices) and all(
            isinstance(device, Mapping) and device.get("available") is True
            and device.get("state") == "off" for device in devices
        )
    return False


def _confirmed_after_read_back(
    pending: Mapping[str, object],
    snapshot: Mapping[str, object],
    observed_at: int,
) -> dict[str, object]:
    rooms = snapshot.get("rooms")
    receipt = {
        **pending,
        "resulting_state_revision": snapshot.get("state_revision"),
        "status": "confirmed",
        "accepted": True,
        "confirmed": True,
        "final": True,
        "duplicate": False,
        "reason": "none",
        "message": "Климатическое действие подтверждено повторным чтением состояния.",
        "read_back": {
            "attempted": True,
            "matched": True,
            "observed_at": observed_at,
            "evidence": {
                "room_count": len(rooms) if isinstance(rooms, list) else 0,
            },
        },
        "updated_at": observed_at,
        "expires_at": max(
            pending["created_at"] + CLIMATE_OPERATION_TTL_MS,
            observed_at,
        ),
    }
    if pending.get("action_snapshot", {}).get("reliability_profile") == "climate_reliability_v1":
        request = ClimateTabletActionRequest(
            request_id=pending["request_id"], correlation_id=pending["correlation_id"],
            expected_state_revision=pending["expected_state_revision"], action=pending["action"],
            room_id=pending["room_id"], parameters=dict(pending["action_parameters"]),
            expected_control_revision=pending["expected_control_revision"],
            reliability_profile="climate_reliability_v1",
        )
        receipt = _reliable_receipt(
            receipt, request, snapshot, pending.get("resulting_control_revision")
        )
    return _validate_receipt(receipt)


def _confirmed_reliable_after_read_back(
    pending: Mapping[str, object],
    request: ClimateTabletActionRequest,
    snapshot: Mapping[str, object],
    ledger: Mapping[str, object],
    metadata: Mapping[tuple[str, str], Mapping[str, object]],
    observed_at: int,
) -> dict[str, object]:
    """Confirm only an already acknowledged ordinary physical attempt."""

    dispatched_at = ledger.get("dispatched_at")
    sources = ledger.get("pre_dispatch_sources")
    if type(dispatched_at) is not int or not isinstance(sources, Mapping):
        raise ClimateTabletUnavailable("climate dispatch ledger is invalid")
    baseline = _metadata_from_reliable_dispatch_ledger(ledger)
    confirmed_room_count = len(sources)
    accepted_count = sum(
        len(device_sources)
        for device_sources in sources.values()
        if isinstance(device_sources, Mapping)
    )
    receipt = {
        **pending,
        "resulting_state_revision": snapshot.get("state_revision"),
        "status": "confirmed",
        "accepted": True,
        "confirmed": True,
        "final": True,
        "duplicate": False,
        "reason": "none",
        "message": "Климатическое действие подтверждено чтением состояния.",
        "read_back": {
            "attempted": True,
            "matched": True,
            "observed_at": observed_at,
            "evidence": {
                "confirmed_room_count": confirmed_room_count,
                "accepted_count": accepted_count,
            },
        },
        "updated_at": observed_at,
        "expires_at": max(
            pending["created_at"] + CLIMATE_OPERATION_TTL_MS,
            observed_at,
        ),
    }
    return _reliable_receipt(
        receipt, request, snapshot,
        pending.get("resulting_control_revision"),
        reliability_metadata=metadata,
        dispatched_at=dispatched_at,
        pre_dispatch_metadata=baseline,
        frozen_execution_outcomes=_terminal_reliable_device_leaves(pending),
    )


def _metadata_from_reliable_dispatch_ledger(
    ledger: Mapping[str, object],
) -> dict[tuple[str, str], dict[str, object]]:
    sources = ledger.get("pre_dispatch_sources")
    if not isinstance(sources, Mapping):
        raise ClimateTabletUnavailable("climate dispatch ledger is invalid")
    baseline: dict[tuple[str, str], dict[str, object]] = {}
    for room_id, device_sources in sources.items():
        if not isinstance(room_id, str) or not isinstance(device_sources, Mapping):
            raise ClimateTabletUnavailable("climate dispatch ledger is invalid")
        for device_id, source_observed_at in device_sources.items():
            if not isinstance(device_id, str) or type(source_observed_at) is not int:
                raise ClimateTabletUnavailable("climate dispatch ledger is invalid")
            baseline[(room_id, device_id)] = {
                "source_observed_at": source_observed_at
            }
    return baseline


def _terminal_reliable_device_leaves(
    receipt: Mapping[str, object],
) -> dict[str, object]:
    """Keep terminal siblings immutable while polling unresolved leaves."""

    frozen: dict[str, object] = {}
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping):
        return frozen
    for room in rooms.values():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping):
            continue
        for device_id, leaf in devices.items():
            if not isinstance(device_id, str) or not isinstance(leaf, Mapping):
                continue
            if leaf.get("execution_state") in {
                "applied", "already_in_sync", "dispatched_not_accepted",
            }:
                frozen[device_id] = dict(leaf)
    return frozen


def _receipt_from_contour_result(
    request: ClimateTabletActionRequest,
    operation_id: str,
    result: object,
    *,
    created_at: int,
    updated_at: int,
    resulting_state_revision: object,
    snapshot: Mapping[str, object] | None = None,
    resulting_control_revision: int | None = None,
    reliability_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    dispatched_at: int | None = None,
    pre_dispatch_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    frozen_scope: Mapping[str, object] | None = None,
) -> dict[str, object]:
    status_value = getattr(result, "status", None)
    status = getattr(status_value, "value", status_value)
    if status not in {item.value for item in ContourApplyStatus}:
        raise ClimateTabletUnavailable("climate command receipt is invalid")
    if status == "confirmed":
        receipt_status = "confirmed"
        reason = "none"
        message = "Климатическое действие подтверждено чтением состояния."
    elif status == "pending":
        receipt_status = "pending"
        reason = "none"
        message = "Команда принята и ожидает подтверждения состояния."
    elif status == "partial":
        receipt_status = "partial"
        reason = "read_back_mismatch"
        message = "Часть климатического действия не подтверждена чтением."
    elif status == "rejected":
        receipt_status = "rejected"
        reason = "action_unsupported"
        message = "Климатическое действие отклонено исполнителем."
    else:
        receipt_status = "unavailable"
        reason = "device_unavailable"
        message = "Результат климатического действия недоступен."
    confirmed = receipt_status == "confirmed"
    pending = receipt_status == "pending"
    accepted = receipt_status in {"confirmed", "pending", "partial"}
    evidence = {
        "confirmed_room_count": getattr(result, "confirmed_room_count", 0),
        "accepted_count": getattr(result, "accepted_count", 0),
    }
    device_outcomes = getattr(result, "device_outcomes", None)
    if isinstance(device_outcomes, Mapping):
        evidence["device_outcomes"] = dict(device_outcomes)
    receipt: dict[str, object] = {
        "contract": {"name": CLIMATE_OPERATION_CONTRACT_NAME, "version": 1},
        "correlation_id": request.correlation_id,
        "operation_id": operation_id,
        "request_id": request.request_id,
        "action": request.action,
        "room_id": request.room_id,
        "expected_state_revision": request.expected_state_revision,
        "resulting_state_revision": resulting_state_revision if confirmed else None,
        "status": receipt_status,
        "accepted": accepted,
        "confirmed": confirmed,
        "final": not pending,
        "duplicate": False,
        "reason": reason,
        "message": message,
        "read_back": {
            "attempted": receipt_status != "rejected",
            "matched": True if confirmed else (None if pending else False),
            "observed_at": updated_at if receipt_status != "rejected" else None,
            "evidence": evidence,
        },
        "created_at": created_at,
        "updated_at": updated_at,
        "expires_at": max(created_at + CLIMATE_OPERATION_TTL_MS, updated_at),
    }
    if request.reliability_profile is not None:
        if isinstance(frozen_scope, Mapping):
            receipt["action_snapshot"] = {"resolved_scope": dict(frozen_scope)}
        return _reliable_receipt(
            receipt,
            request,
            snapshot or {},
            resulting_control_revision,
            reliability_metadata=reliability_metadata,
            dispatched_at=dispatched_at,
            pre_dispatch_metadata=pre_dispatch_metadata,
        )
    return receipt


def _terminal_receipt(
    request: ClimateTabletActionRequest,
    operation_id: str,
    *,
    status: str,
    reason: str,
    message: str,
    created_at: int,
    updated_at: int,
    snapshot: Mapping[str, object] | None = None,
    resulting_control_revision: int | None = None,
) -> dict[str, object]:
    receipt: dict[str, object] = {
        "contract": {"name": CLIMATE_OPERATION_CONTRACT_NAME, "version": 1},
        "correlation_id": request.correlation_id,
        "operation_id": operation_id,
        "request_id": request.request_id,
        "action": request.action,
        "room_id": request.room_id,
        "expected_state_revision": request.expected_state_revision,
        "resulting_state_revision": None,
        "status": status,
        "accepted": status == "timed_out",
        "confirmed": False,
        "final": True,
        "duplicate": False,
        "reason": reason,
        "message": message[:500] or "Климатическое действие не выполнено.",
        "read_back": {
            "attempted": status == "timed_out",
            "matched": False if status == "timed_out" else None,
            "observed_at": updated_at if status == "timed_out" else None,
            "evidence": {},
        },
        "created_at": created_at,
        "updated_at": updated_at,
        "expires_at": max(created_at + CLIMATE_OPERATION_TTL_MS, updated_at),
    }
    if request.reliability_profile is not None:
        return _reliable_receipt(
            receipt,
            request,
            snapshot or {},
            resulting_control_revision,
        )
    return receipt


def _legacy_home_execution_fact(
    receipt: Mapping[str, object], request: ClimateTabletActionRequest,
    native_result: object | None,
) -> dict[str, object]:
    """Persist only the private execution facts required by legacy replay."""

    operation_id = receipt.get("operation_id")
    if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
        raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
    facts = _legacy_home_typed_execution_facts(receipt)
    return {
        "operation_id": operation_id,
        "request_id": request.request_id,
        "correlation_id": request.correlation_id,
        "request_fingerprint": request.fingerprint,
        "parameters_fingerprint": _canonical_fingerprint(request.parameters),
        **facts,
        "changes": _legacy_home_changes(native_result),
        "humidity_changes": _native_change_count(native_result, "humidity_changes"),
        "read_back": receipt.get("read_back", {}),
        "reasons": facts["reasons"],
    }


def _legacy_home_typed_execution_facts(receipt: Mapping[str, object]) -> dict[str, object]:
    """Reduce immutable typed leaves to the legacy aggregate exactly once."""

    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping) or not rooms:
        raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
    command_count = accepted_count = confirmed_room_count = 0
    reasons: list[str] = []
    for room in rooms.values():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping) or not devices:
            raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
        leaves = tuple(devices.values())
        if not all(isinstance(leaf, Mapping) for leaf in leaves):
            raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
        if all(leaf.get("status") == "confirmed" for leaf in leaves):
            confirmed_room_count += 1
        for leaf in leaves:
            command = leaf.get("command_count")
            accepted = leaf.get("accepted_count")
            if type(command) is not int or command < 0 or type(accepted) is not int or accepted < 0 or accepted > command:
                raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
            command_count += command
            accepted_count += accepted
            reason = leaf.get("reason")
            if isinstance(reason, str):
                reasons.append(reason)
    status = receipt.get("status")
    if status == "timed_out":
        status = "unavailable"
    if status not in {"confirmed", "pending", "partial", "rejected", "unavailable"}:
        raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
    return {
        "status": status, "room_count": len(rooms), "command_count": command_count,
        "accepted_count": accepted_count, "confirmed_room_count": confirmed_room_count,
        "reasons": _legacy_home_reason_codes(reasons),
    }


def _legacy_home_changes(native_result: object | None) -> dict[str, int]:
    """Keep actual native deltas, never infer a change from room scope."""

    return {
        "temperature": _native_change_count(native_result, "temperature_changes"),
        "strategy": _native_change_count(native_result, "strategy_changes"),
        "automatic_mode": _native_change_count(native_result, "automatic_mode_changes"),
    }


def _native_change_count(native_result: object | None, field: str) -> int:
    value = getattr(native_result, field, None)
    if type(value) is not int or value < 0:
        raise ClimateTabletUnavailable("legacy climate execution facts are invalid")
    return value


def _legacy_home_reason_codes(reasons: list[str]) -> list[str]:
    """Use only the public legacy reason vocabulary in persisted facts."""

    mapping = {
        "already_in_sync": "already_in_sync",
        "engine_rejected": "engine_rejected",
        "command_result_unavailable": "command_result_unavailable",
        "command_failed": "command_result_unavailable",
        "configuration_error": "command_result_unavailable",
        "device_unavailable": "command_result_unavailable",
        "verification_unavailable": "verification_unavailable",
        "state_not_confirmed": "state_not_confirmed",
        "state_stale": "verification_unavailable",
    }
    return list(dict.fromkeys(mapping[reason] for reason in reasons if reason in mapping))


def _valid_legacy_home_execution_fact(value: object) -> bool:
    """Keep the compatibility sidecar bounded and non-dispatchable."""

    if not isinstance(value, Mapping) or set(value) != {
        "operation_id", "request_id", "correlation_id", "request_fingerprint",
        "parameters_fingerprint", "status", "room_count", "command_count",
        "accepted_count", "confirmed_room_count", "changes", "humidity_changes", "read_back", "reasons",
    }:
        return False
    return bool(
        isinstance(value.get("operation_id"), str)
        and _OPERATION_ID.fullmatch(value["operation_id"]) is not None
        and isinstance(value.get("request_id"), str)
        and _REQUEST_ID.fullmatch(value["request_id"]) is not None
        and isinstance(value.get("correlation_id"), str)
        and _REQUEST_ID.fullmatch(value["correlation_id"]) is not None
        and all(
            isinstance(value.get(key), str)
            and re.fullmatch(r"[a-f0-9]{64}", value[key]) is not None
            for key in ("request_fingerprint", "parameters_fingerprint")
        )
        and value.get("status") in {
            "confirmed", "pending", "partial", "rejected", "unavailable", "timed_out",
        }
        and all(
            type(value.get(key)) is int and value[key] >= 0
            for key in (
                "room_count", "command_count", "accepted_count",
                "confirmed_room_count",
            )
        )
        and isinstance(value.get("read_back"), Mapping)
        and isinstance(value.get("changes"), Mapping)
        and set(value["changes"]) == {"temperature", "strategy", "automatic_mode"}
        and all(type(item) is int and item >= 0 for item in value["changes"].values())
        and type(value.get("humidity_changes")) is int and value["humidity_changes"] >= 0
        and isinstance(value.get("reasons"), list)
        and all(isinstance(reason, str) for reason in value["reasons"])
    )


def _legacy_home_execution_fact_matches_record(
    correlation_id: object,
    fact: object,
    record: _StoredOperation | None,
) -> bool:
    """Bind the private legacy projection to exactly one durable command."""

    return bool(
        isinstance(correlation_id, str)
        and _REQUEST_ID.fullmatch(correlation_id) is not None
        and _valid_legacy_home_execution_fact(fact)
        and isinstance(record, _StoredOperation)
        and fact["correlation_id"] == correlation_id
        and fact["request_id"] == record.request.request_id
        and fact["request_fingerprint"] == record.fingerprint
        and fact["operation_id"] == record.receipt.get("operation_id")
        and record.request.action == "set_home_targets"
        and record.request.correlation_id == correlation_id
        and fact["parameters_fingerprint"] == _canonical_fingerprint(
            record.request.parameters
        )
    )


def _pending_predecessor(
    records: object,
    request: ClimateTabletActionRequest,
    snapshot: Mapping[str, object],
) -> _StoredOperation | None:
    """Find only an exact, wholly pre-dispatch reliability predecessor."""
    scope = _resolved_scope(snapshot, request)
    for record in records:  # type: ignore[union-attr]
        receipt = record.receipt
        ledger = record.dispatch_ledger
        action_snapshot = receipt.get("action_snapshot")
        outcomes = receipt.get("outcomes")
        if (
            receipt.get("final") is not False
            or receipt.get("action") != request.action
            or receipt.get("resulting_control_revision") != request.expected_control_revision
            or not isinstance(action_snapshot, Mapping)
            or action_snapshot.get("resolved_scope") != scope
            or not isinstance(outcomes, Mapping)
            or not isinstance(ledger, Mapping)
            or ledger.get("state") != "pending_dispatch"
        ):
            continue
        rooms = outcomes.get("rooms")
        if not isinstance(rooms, Mapping):
            continue
        leaves = [leaf for room in rooms.values() if isinstance(room, Mapping)
                  for leaf in (room.get("devices", {}).values() if isinstance(room.get("devices"), Mapping) else ())]
        if leaves and all(
            isinstance(leaf, Mapping)
            and leaf.get("execution_state") == "pending_dispatch"
            and _strict_leaf_counts(leaf, 0, 0)
            for leaf in leaves
        ):
            return record
    return None


def _superseded_receipt(
    receipt: Mapping[str, object],
    successor_operation_id: str,
    successor_fingerprint: str,
    now: int,
) -> dict[str, object]:
    """Freeze a pre-dispatch receipt as superseded without inventing calls."""
    result = dict(receipt)
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    rendered_rooms: dict[str, object] = {}
    if isinstance(rooms, Mapping):
        for room_id, room in rooms.items():
            devices = room.get("devices") if isinstance(room, Mapping) else None
            rendered_devices = {
                device_id: {
                    "status": "not_attempted", "reason": "none",
                    "execution_state": "superseded_by_newer_intent",
                    "command_count": 0, "accepted_count": 0,
                    "message_code": "superseded",
                    "message": "Команда заменена более новой сохранённой целью.",
                }
                for device_id in devices if isinstance(devices, Mapping)
            }
            rendered_rooms[room_id] = {
                "status": "not_attempted", "reason": "none",
                "execution_state": "superseded_by_newer_intent",
                "message_code": "superseded",
                "message": "Команда заменена более новой сохранённой целью.",
                "devices": rendered_devices,
            }
    intent = dict(receipt.get("intent", {}))
    intent.update(
        status="superseded_by_newer_intent",
        superseded_by_operation_id=successor_operation_id,
        superseded_by_request_fingerprint=successor_fingerprint,
    )
    result.update(
        status="partial", accepted=True, confirmed=False, final=True,
        reason="operation_pending", message_code="partial",
        message="Цель заменена более новой сохранённой целью.",
        unfinished_device_count=0, updated_at=now,
        read_back={"attempted": False, "matched": None, "observed_at": None, "evidence": {}},
        intent=intent, outcomes={"rooms": rendered_rooms},
    )
    return _validate_receipt(result)


def _expired_pending_reliable_receipt(
    receipt: Mapping[str, object], now: int, *, blocked_immediately: bool = False,
) -> dict[str, object]:
    """Close an unstarted intent without claiming a physical acceptance."""

    result = dict(receipt)
    outcomes = receipt.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    rendered_rooms: dict[str, object] = {}
    if isinstance(rooms, Mapping):
        for room_id, room in rooms.items():
            devices = room.get("devices") if isinstance(room, Mapping) else None
            rendered_devices = {
                device_id: {
                    "status": "not_attempted",
                    "reason": "configuration_error",
                    "execution_state": "blocked_before_dispatch",
                    "command_count": 0,
                    "accepted_count": 0,
                    "message_code": "configuration_error",
                    "message": (
                        "Конфигурация устройства требует проверки."
                        if blocked_immediately
                        else "Время ожидания истекло до отправки команды."
                    ),
                }
                for device_id in devices if isinstance(devices, Mapping)
            }
            rendered_rooms[room_id] = {
                "status": "not_attempted",
                "reason": "configuration_error",
                "execution_state": "blocked_before_dispatch",
                "message_code": "configuration_error",
                "message": (
                    "Конфигурация устройства требует проверки."
                    if blocked_immediately
                    else "Время ожидания истекло до отправки команды."
                ),
                "devices": rendered_devices,
            }
    intent = dict(receipt.get("intent", {}))
    intent["status"] = (
        "saved_blocked_before_dispatch"
        if blocked_immediately
        else "saved_apply_failed"
    )
    result.update(
        status="partial", accepted=True, confirmed=False, final=True,
        reason="read_back_mismatch", message_code="partial",
        message="Цель сохранена, часть устройств ожидает применения.",
        unfinished_device_count=0, updated_at=now,
        expires_at=max(receipt.get("expires_at", now), now),
        read_back={"attempted": False, "matched": None, "observed_at": None, "evidence": {}},
        intent=intent, outcomes={"rooms": rendered_rooms},
    )
    return _validate_receipt(result)


def _ambiguous_started_reliable_receipt(
    receipt: Mapping[str, object],
    request: ClimateTabletActionRequest,
    now: int,
) -> dict[str, object]:
    """Freeze an interrupted post-boundary operation without a retry claim."""

    scope = receipt.get("action_snapshot")
    scope = scope.get("resolved_scope") if isinstance(scope, Mapping) else None
    rows = scope.get("devices_by_room") if isinstance(scope, Mapping) else None
    execution: dict[str, object] = {}
    if not isinstance(rows, list):
        raise ClimateTabletUnavailable("climate reliable scope is invalid")
    for row in rows:
        device_ids = row.get("device_ids") if isinstance(row, Mapping) else None
        if not isinstance(device_ids, list):
            raise ClimateTabletUnavailable("climate reliable scope is invalid")
        for device_id in device_ids:
            if not isinstance(device_id, str):
                raise ClimateTabletUnavailable("climate reliable scope is invalid")
            execution[device_id] = {
                "status": "failed",
                "reason": "command_failed",
                "execution_state": "dispatched_not_accepted",
                "command_count": 1,
                "accepted_count": 0,
                "retry_policy": "forbidden_after_dispatch",
            }
    result = dict(receipt)
    result.update({
        "status": "partial",
        "accepted": True,
        "confirmed": False,
        "final": True,
        "reason": "read_back_mismatch",
        "message": "Цель сохранена, часть устройств ожидает применения.",
        "read_back": {
            "attempted": True,
            "matched": False,
            "observed_at": None,
            "evidence": {"device_outcomes": execution},
        },
        "updated_at": now,
        "expires_at": max(receipt.get("expires_at", now), now),
    })
    return _reliable_receipt(
        result, request, _snapshot_from_reliable_scope(receipt),
        receipt.get("resulting_control_revision"),
        frozen_execution_outcomes=execution,
    )


def _expire_accepted_reliable_receipt(
    receipt: Mapping[str, object], now: int,
) -> dict[str, object]:
    """Timeout only leaves whose physical acceptance is still unresolved."""

    result = json.loads(json.dumps(receipt, ensure_ascii=False))
    outcomes = result.get("outcomes")
    rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
    if not isinstance(rooms, Mapping):
        raise ClimateTabletUnavailable("climate reliable outcomes are invalid")
    pending_count = 0
    for room in rooms.values():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping):
            raise ClimateTabletUnavailable("climate reliable outcomes are invalid")
        for leaf in devices.values():
            if not isinstance(leaf, dict):
                raise ClimateTabletUnavailable("climate reliable outcomes are invalid")
            if leaf.get("execution_state") == "accepted_unverified":
                pending_count += 1
                leaf.update({
                    "status": "failed",
                    "reason": "command_failed",
                    "execution_state": "accepted_timeout",
                    "command_count": 1,
                    "accepted_count": 1,
                    "retry_policy": "forbidden_after_dispatch",
                    "message_code": "command_failed",
                    "message": "Команда не подтверждена, требуется проверка устройства.",
                })
    if pending_count == 0:
        raise ClimateTabletUnavailable("climate reliable timeout is invalid")
    all_timeout = True
    mixed = False
    for room in rooms.values():
        devices = room.get("devices") if isinstance(room, Mapping) else {}
        states = {
            (
                leaf.get("status"), leaf.get("reason"),
                leaf.get("execution_state"), leaf.get("message_code"),
                leaf.get("message"),
            )
            for leaf in devices.values()
            if isinstance(leaf, Mapping)
        } if isinstance(devices, Mapping) else set()
        statuses = {state[0] for state in states}
        if not isinstance(devices, Mapping) or any(
            not isinstance(leaf, Mapping)
            or leaf.get("execution_state") != "accepted_timeout"
            for leaf in devices.values()
        ):
            all_timeout = False
        if len(statuses) > 1:
            mixed = True
        if isinstance(room, dict) and isinstance(devices, Mapping):
            if statuses == {"confirmed"}:
                room.update(status="confirmed", reason="none", execution_state="applied",
                            message_code="confirmed", message="Результат подтверждён чтением состояния.")
            elif len(states) > 1:
                room.update(status="partial", reason="none", message_code="partial",
                            message="Результаты устройств различаются.")
                room.pop("execution_state", None)
            else:
                first = next(iter(devices.values()), {})
                if isinstance(first, Mapping):
                    room.update(status=first.get("status"), reason=first.get("reason"),
                                execution_state=first.get("execution_state"),
                                message_code=first.get("message_code"), message=first.get("message"))
    intent = result.get("intent")
    if isinstance(intent, dict):
        intent["status"] = "saved_apply_failed"
    result.update(
        status="timed_out" if all_timeout and not mixed else "partial",
        confirmed=False,
        final=True,
        reason="confirmation_timeout" if all_timeout and not mixed else "read_back_mismatch",
        message_code="timed_out" if all_timeout and not mixed else "partial",
        message=("Время подтверждения истекло." if all_timeout and not mixed
                 else "Цель сохранена, часть устройств ожидает применения."),
        unfinished_device_count=0,
        updated_at=now,
        expires_at=max(result.get("expires_at", now), now),
    )
    return _validate_receipt(result)


def _reliable_receipt(
    receipt: dict[str, object],
    request: ClimateTabletActionRequest,
    snapshot: Mapping[str, object],
    resulting_control_revision: int | None,
    *,
    reliability_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    dispatched_at: int | None = None,
    pre_dispatch_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    frozen_execution_outcomes: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Attach the negotiated receipt surface without changing legacy replies.

    The reliability profile is opt-in.  Its payload is deliberately derived
    from the authoritative snapshot and the already validated request, so a
    retry, polling call or restart cannot invent a different scope or desired
    value after a command has been reserved.
    """
    previous_snapshot = receipt.get("action_snapshot")
    previous_scope = (
        previous_snapshot.get("resolved_scope")
        if isinstance(previous_snapshot, Mapping)
        else None
    )
    scope = (
        dict(previous_scope)
        if isinstance(previous_scope, Mapping)
        and isinstance(previous_scope.get("devices_by_room"), list)
        else _resolved_scope(snapshot, request)
    )
    status = receipt["status"]
    message_code = {
        "confirmed": "confirmed", "partial": "partial", "pending": "pending",
        "rejected": "rejected", "unavailable": "unavailable", "timed_out": "timed_out",
    }[status]
    message = {
        "confirmed": "Результат подтверждён чтением состояния.",
        "partial": "Цель сохранена, часть устройств ожидает применения.",
        "pending": "Команда принята и ожидает подтверждения.",
        "rejected": "Команда отклонена.",
        "unavailable": "Результат команды пока недоступен.",
        "timed_out": "Время подтверждения истекло.",
    }[status]
    # ``attempted`` is set only by the native execution receipt.  The first
    # durable reservation intentionally has it false, so callers can tell a
    # pre-dispatch saved intent (0/0) from an accepted physical leaf (1/1).
    dispatched = receipt.get("read_back", {}).get("attempted") is True if isinstance(receipt.get("read_back"), Mapping) else False
    execution = (
        frozen_execution_outcomes
        if isinstance(frozen_execution_outcomes, Mapping)
        else receipt.get("read_back", {}).get("evidence", {}).get("device_outcomes", {})
        if isinstance(receipt.get("read_back"), Mapping)
        and isinstance(receipt.get("read_back", {}).get("evidence"), Mapping)
        else {}
    )
    outcomes, unfinished = _reliable_outcomes(
        request, snapshot, scope, status, dispatched=dispatched,
        execution_outcomes=execution, reliability_metadata=reliability_metadata,
        dispatched_at=dispatched_at,
        pre_dispatch_metadata=pre_dispatch_metadata,
    )
    intent_status = {
        "confirmed": "saved_and_applied",
        "pending": "saved_pending_confirmation",
        "partial": "saved_pending_confirmation",
        "timed_out": "saved_apply_failed",
        "rejected": "unsaved_rejected",
        "unavailable": "unsaved_unavailable",
    }[status]
    result = dict(receipt)
    result.update({
        "action_parameters": dict(request.parameters),
        "action_snapshot": {
            "contract": {"name": CLIMATE_ACTION_CONTRACT_NAME, "version": 1},
            "reliability_profile": "climate_reliability_v1",
            "request_id": request.request_id,
            "correlation_id": request.correlation_id,
            "request_fingerprint": request.fingerprint,
            "action": request.action,
            "room_id": request.room_id,
            "expected_state_revision": request.expected_state_revision,
            "expected_control_revision": request.expected_control_revision,
            "parameters": dict(request.parameters),
            "resolved_scope": scope,
        },
        "expected_control_revision": request.expected_control_revision,
        "resulting_control_revision": (
            resulting_control_revision if receipt["accepted"] is True else None
        ),
        "unfinished_device_count": unfinished,
        "message_code": message_code,
        "message": message,
        "request_fingerprint": request.fingerprint,
        "confirmation_window_ms": min(CLIMATE_OPERATION_TTL_MS, 30_000),
        "intent": {
            "status": intent_status,
            "request_fingerprint": request.fingerprint,
            "control_revision": resulting_control_revision or 0,
            "scope_revision": resulting_control_revision or 0,
            "scope_fingerprint": _canonical_fingerprint(scope),
            "resolved_scope": scope,
            "desired_target_temperature": request.parameters.get("target_temperature"),
            "desired_target_humidity": request.parameters.get("target_humidity"),
        },
        "outcomes": {"rooms": outcomes},
    })
    if status in {"rejected", "unavailable"}:
        return result
    accepted_leaf_count = 0
    confirmed_room_count = 0
    for room in outcomes.values():
        devices = room.get("devices") if isinstance(room, Mapping) else None
        if not isinstance(devices, Mapping):
            continue
        leaf_values = [leaf for leaf in devices.values() if isinstance(leaf, Mapping)]
        accepted_leaf_count += sum(
            leaf.get("execution_state") in {
                "applied", "accepted_unverified", "accepted_timeout"
            }
            for leaf in leaf_values
        )
        if leaf_values and all(leaf.get("status") == "confirmed" for leaf in leaf_values):
            confirmed_room_count += 1
    read_back = result.get("read_back")
    if isinstance(read_back, Mapping):
        evidence = read_back.get("evidence")
        if isinstance(evidence, Mapping):
            result["read_back"] = {
                **read_back,
                "evidence": {
                    **{
                        key: value for key, value in evidence.items()
                        if key != "device_outcomes"
                    },
                    "accepted_count": accepted_leaf_count,
                    "confirmed_room_count": confirmed_room_count,
                },
            }
    leaves = _reliable_receipt_leaves(result) or []
    all_confirmed = bool(leaves) and all(
        leaf.get("status") == "confirmed" for leaf in leaves
    )
    # A reliability partial is intentionally pollable.  It represents saved
    # desired state with unfinished leaves, never a false terminal success.
    if all_confirmed:
        result.update(
            status="confirmed",
            confirmed=True,
            final=True,
            reason="none",
            message_code="confirmed",
            message="Результат подтверждён чтением состояния.",
        )
    elif unfinished:
        if result["status"] == "confirmed":
            result.update(
                status="partial",
                confirmed=False,
                reason="read_back_mismatch",
                message_code="partial",
                message="Цель сохранена, часть устройств ожидает применения.",
                read_back={
                    "attempted": True,
                    "matched": False,
                    "observed_at": result["updated_at"],
                    "evidence": result.get("read_back", {}).get("evidence", {}),
                },
            )
        result["final"] = False
        if status == "partial":
            result["reason"] = "read_back_mismatch"
    elif not all_confirmed:
        result.update(
            status="partial",
            confirmed=False,
            final=True,
            reason="read_back_mismatch",
            message_code="partial",
            message="Цель сохранена, часть устройств ожидает применения.",
            read_back={
                **result.get("read_back", {}),
                "attempted": True,
                "matched": False,
            },
        )
    intent = result.get("intent")
    if isinstance(intent, Mapping):
        if result.get("final") is True and result.get("status") == "confirmed":
            intent_status = "saved_and_applied"
        elif result.get("final") is True:
            intent_status = "saved_apply_failed"
        else:
            intent_status = "saved_pending_confirmation"
        result["intent"] = {**intent, "status": intent_status}
    return result


def _resolved_scope(
    snapshot: Mapping[str, object], request: ClimateTabletActionRequest
) -> dict[str, object]:
    rooms = snapshot.get("rooms")
    selected = (
        [room for room in rooms if isinstance(room, Mapping)]
        if request.room_id is None and isinstance(rooms, list)
        else [room for room in rooms if isinstance(room, Mapping) and room.get("id") == request.room_id]
        if isinstance(rooms, list) else []
    )
    rows: list[dict[str, object]] = []
    for room in selected:
        room_id = room.get("id")
        devices = room.get("devices")
        if not isinstance(room_id, str) or not isinstance(devices, list):
            continue
        ids = [
            device.get("id") for device in devices
            if isinstance(device, Mapping)
            and isinstance(device.get("id"), str)
            and _device_supports_action(device, request)
        ]
        if request.action == "set_device_mode":
            ids = [item for item in ids if item == request.parameters.get("device_id")]
        if ids:
            ordered = sorted(ids)
            rows.append({"room_id": room_id, "device_ids": ordered})
    rows.sort(key=lambda row: str(row["room_id"]))
    device_ids = [
        device_id
        for row in rows
        for device_id in row["device_ids"]
        if isinstance(device_id, str)
    ]
    return {"room_ids": [row["room_id"] for row in rows], "device_ids": device_ids, "devices_by_room": rows}


def _reliable_scope_size(
    snapshot: Mapping[str, object], request: ClimateTabletActionRequest,
    *, frozen_scope: Mapping[str, object] | None = None,
) -> int:
    scope = frozen_scope if isinstance(frozen_scope, Mapping) else _resolved_scope(snapshot, request)
    device_ids = scope.get("device_ids")
    return len(device_ids) if isinstance(device_ids, list) else 0


def _has_exact_reliable_device_outcomes(
    result: object,
    snapshot: Mapping[str, object],
    request: ClimateTabletActionRequest,
    *, frozen_scope: Mapping[str, object] | None = None,
    reliability_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> bool:
    """Return whether an aggregate result names every resolved device leaf."""

    scope = frozen_scope if isinstance(frozen_scope, Mapping) else _resolved_scope(snapshot, request)
    if not isinstance(scope, Mapping) or not _valid_frozen_scope(scope, request):
        return False
    expected = scope.get("device_ids")
    outcomes = getattr(result, "device_outcomes", None)
    if not isinstance(expected, list) or not isinstance(outcomes, Mapping):
        return False
    if set(outcomes) != set(expected):
        return False
    rooms = {
        room.get("id"): room
        for room in snapshot.get("rooms", [])
        if isinstance(room, Mapping) and isinstance(room.get("id"), str)
    } if isinstance(snapshot.get("rooms"), list) else {}
    room_by_device = {
        device_id: row.get("room_id")
        for row in scope.get("devices_by_room", [])
        if isinstance(row, Mapping)
        and isinstance(row.get("room_id"), str)
        and isinstance(row.get("device_ids"), list)
        for device_id in row["device_ids"]
        if isinstance(device_id, str)
    }
    command_count = 0
    accepted_count = 0
    for device_id in expected:
        leaf = outcomes.get(device_id)
        if not isinstance(device_id, str) or not isinstance(leaf, Mapping):
            return False
        execution = leaf.get("execution_state")
        commands = leaf.get("command_count")
        accepted = leaf.get("accepted_count")
        if type(commands) is not int or type(accepted) is not int:
            return False
        if execution == "accepted_unverified" and (commands, accepted) != (1, 1):
            return False
        if execution == "dispatched_not_accepted" and (commands, accepted) != (1, 0):
            return False
        if execution == "blocked_before_dispatch" and (
            (commands, accepted) != (0, 0)
            or leaf.get("status") != "not_attempted"
            or leaf.get("reason") != "configuration_error"
            or leaf.get("message_code") != "configuration_error"
        ):
            return False
        if execution == "already_in_sync" and (
            (commands, accepted) != (0, 0)
            or leaf.get("status") != "confirmed"
            or not _valid_already_in_sync_evidence(
                leaf,
                request,
                device_id,
                rooms.get(room_by_device.get(device_id)),
                snapshot,
                reliability_metadata.get((room_by_device.get(device_id), device_id), {})
                if isinstance(reliability_metadata, Mapping)
                and isinstance(room_by_device.get(device_id), str)
                else {},
            )
        ):
            return False
        if (
            execution in {"accepted_unverified", "dispatched_not_accepted"}
            and leaf.get("retry_policy") != "forbidden_after_dispatch"
        ):
            return False
        if execution not in {
            "accepted_unverified",
            "dispatched_not_accepted",
            "applied",
            "already_in_sync",
            "blocked_before_dispatch",
        }:
            return False
        command_count += commands
        accepted_count += accepted
    result_command_count = getattr(result, "command_count", None)
    result_accepted_count = getattr(result, "accepted_count", None)
    return (
        type(result_command_count) is int
        and type(result_accepted_count) is int
        and (result_command_count, result_accepted_count)
        == (command_count, accepted_count)
    )


def _has_trusted_terminal_blocked_outcomes(result: object) -> bool:
    """Accept only an explicit zero-call, pre-dispatch terminal map."""

    outcomes = getattr(result, "device_outcomes", None)
    return isinstance(outcomes, Mapping) and bool(outcomes) and all(
        isinstance(leaf, Mapping)
        and leaf.get("status") == "not_attempted"
        and leaf.get("reason") == "configuration_error"
        and leaf.get("execution_state") == "blocked_before_dispatch"
        and leaf.get("message_code") == "configuration_error"
        and _strict_leaf_counts(leaf, 0, 0)
        for leaf in outcomes.values()
    )


def _valid_already_in_sync_evidence(
    leaf: Mapping[str, object],
    request: ClimateTabletActionRequest,
    device_id: str,
    room: object,
    snapshot: Mapping[str, object],
    metadata: Mapping[str, object],
) -> bool:
    """Bind a zero-call confirmation to the current authoritative read-back."""

    evidence = leaf.get("evidence")
    if not isinstance(room, Mapping) or not isinstance(evidence, Mapping):
        return False
    devices = room.get("devices")
    device = next(
        (
            item for item in devices
            if isinstance(item, Mapping) and item.get("id") == device_id
        ),
        None,
    ) if isinstance(devices, list) else None
    if not isinstance(device, Mapping):
        return False
    expected = _reliable_evidence(request, room, device, snapshot, metadata)
    action = evidence.get("action")
    source_observed_at = metadata.get("source_observed_at")
    actual = evidence.get("observed_actual")
    expected_actual = expected.get("observed_actual")
    if not isinstance(actual, Mapping) or not isinstance(expected_actual, Mapping):
        return False
    relevant_actual_keys = {
        "set_room_target": {"desired_target_temperature", "reported_target_temperature"},
        "set_room_humidity_target": {"desired_target_humidity", "reported_target_humidity"},
        "set_room_min_target": {"desired_minimum_temperature", "reported_minimum_temperature"},
        "set_room_target_strategy": {"desired_target_strategy", "reported_target_strategy"},
        "set_room_mode": {"desired_mode", "reported_mode"},
        "set_device_mode": {"desired_mode", "reported_mode"},
        "turn_room_off": {"desired_state", "reported_state"},
        "clear_room_override": {"desired_override_state", "reported_override_state"},
        "synchronize_home": {"desired_synchronization", "reported_synchronization"},
    }.get(request.action, set())
    if request.action == "set_home_targets":
        relevant_actual_keys = {
            key
            for key in (
                "desired_target_temperature",
                "reported_target_temperature",
                "desired_target_humidity",
                "reported_target_humidity",
            )
            if key.removeprefix("desired_") in request.parameters
            or key.removeprefix("reported_") in request.parameters
        }
    return (
        snapshot.get("fresh") is True
        and evidence.get("fresh") is True
        and type(source_observed_at) is int
        and 1 <= source_observed_at <= 9_007_199_254_740_991
        and type(evidence.get("observed_at")) is int
        and evidence.get("observed_at") == source_observed_at
        and evidence.get("desired_target_temperature")
        == expected.get("desired_target_temperature")
        and evidence.get("desired_target_humidity")
        == expected.get("desired_target_humidity")
        and all(actual.get(key) == expected_actual.get(key) for key in relevant_actual_keys)
        and evidence.get("reported_target_temperature")
        == expected.get("reported_target_temperature")
        and evidence.get("reported_target_humidity")
        == expected.get("reported_target_humidity")
        and isinstance(action, Mapping)
        and action == {
            "request_fingerprint": request.fingerprint,
            "action": request.action,
            "parameters": dict(request.parameters),
        }
        and _reliable_evidence_matches_request(evidence, request)
    )


def _device_supports_action(
    device: Mapping[str, object], request: ClimateTabletActionRequest
) -> bool:
    """Keep reliable leaves equal to the executor's command-capable scope."""

    # The public runtime intentionally omits raw HA capabilities.  Its stable
    # device kind is the contract-level axis declaration used by the executor.
    kind = device.get("kind")
    temperature = kind in {
        "air_conditioner", "radiator_thermostat", "floor_heating",
    }
    humidity = kind == "humidifier"
    powered = kind in {
        "air_conditioner", "humidifier", "radiator_thermostat", "floor_heating",
    }
    mode = kind in {"air_conditioner", "radiator_thermostat"}
    if request.action == "set_device_mode":
        return device.get("id") == request.parameters.get("device_id") and mode
    if request.action in {"set_room_target", "clear_room_override", "set_room_min_target", "set_room_target_strategy"}:
        return temperature
    if request.action == "set_room_humidity_target":
        return humidity
    if request.action == "set_home_targets":
        return (
            ("target_temperature" in request.parameters and temperature)
            or ("target_humidity" in request.parameters and humidity)
        )
    if request.action == "set_room_mode":
        return mode
    if request.action == "turn_room_off":
        return powered
    if request.action == "synchronize_home":
        return powered
    return False


def _snapshot_from_reliable_scope(receipt: Mapping[str, object]) -> dict[str, object]:
    """Rebuild only the durable resolved scope needed by timeout polling."""
    action_snapshot = receipt.get("action_snapshot")
    if not isinstance(action_snapshot, Mapping):
        return {}
    scope = action_snapshot.get("resolved_scope")
    if not isinstance(scope, Mapping) or not isinstance(scope.get("devices_by_room"), list):
        return {}
    rooms = []
    for row in scope["devices_by_room"]:
        if not isinstance(row, Mapping) or not isinstance(row.get("room_id"), str):
            continue
        device_ids = row.get("device_ids")
        if not isinstance(device_ids, list):
            continue
        rooms.append({"id": row["room_id"], "devices": [{"id": item} for item in device_ids if isinstance(item, str)]})
    return {"rooms": rooms, "generated_at": receipt.get("updated_at", 0)}


def _reliable_outcomes(
    request: ClimateTabletActionRequest,
    snapshot: Mapping[str, object],
    scope: Mapping[str, object],
    status: object,
    *,
    dispatched: bool = False,
    execution_outcomes: Mapping[str, object] | None = None,
    reliability_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
    dispatched_at: int | None = None,
    pre_dispatch_metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> tuple[dict[str, object], int]:
    room_index = {item.get("id"): item for item in snapshot.get("rooms", []) if isinstance(item, Mapping)} if isinstance(snapshot.get("rooms"), list) else {}
    rooms: dict[str, object] = {}
    unfinished = 0
    for row in scope["devices_by_room"]:
        room_id = row["room_id"]
        room = room_index.get(room_id, {})
        devices = {item.get("id"): item for item in room.get("devices", []) if isinstance(item, Mapping)} if isinstance(room, Mapping) and isinstance(room.get("devices"), list) else {}
        leaves: dict[str, object] = {}
        for device_id in row["device_ids"]:
            device = devices.get(device_id, {})
            execution_leaf = execution_outcomes.get(device_id) if isinstance(execution_outcomes, Mapping) else None
            if (
                isinstance(execution_leaf, Mapping)
                and execution_leaf.get("status") == "not_attempted"
                and execution_leaf.get("reason") == "configuration_error"
                and execution_leaf.get("execution_state") == "blocked_before_dispatch"
                and _strict_leaf_counts(execution_leaf, 0, 0)
            ):
                leaves[device_id] = dict(execution_leaf)
                continue
            if (
                isinstance(execution_leaf, Mapping)
                and execution_leaf.get("status") == "confirmed"
                and execution_leaf.get("execution_state")
                in {"applied", "already_in_sync"}
                and isinstance(execution_leaf.get("evidence"), Mapping)
            ):
                leaf = dict(execution_leaf)
                # Native executor evidence is intentionally minimal.  Before
                # persisting a reliable receipt, bind the leaf to the exact
                # tablet request and the authoritative post-dispatch view.
                # Otherwise the service could create a receipt it rejects on
                # its own restart.
                leaf["evidence"] = _reliable_evidence(
                    request,
                    room if isinstance(room, Mapping) else {},
                    device if isinstance(device, Mapping) else {},
                    snapshot,
                    reliability_metadata.get((room_id, device_id), {})
                    if isinstance(reliability_metadata, Mapping) else {},
                )
                if leaf.get("execution_state") == "already_in_sync":
                    # The public reliability contract intentionally uses one
                    # stable confirmation vocabulary for both a read-back
                    # after dispatch and an authoritative zero-call match.
                    leaf["message_code"] = "confirmed"
                    leaf["message"] = "Результат подтверждён чтением состояния."
                leaves[device_id] = leaf
                continue
            if isinstance(execution_leaf, Mapping) and execution_leaf.get("execution_state") == "dispatched_not_accepted":
                leaf_status, reason, execution, code, message = ("failed", "command_failed", "dispatched_not_accepted", "command_failed", "Команда не подтверждена, требуется проверка устройства.")
            elif (
                isinstance(execution_leaf, Mapping)
                and execution_leaf.get("execution_state") == "accepted_unverified"
                and status != "confirmed"
            ):
                leaf_status, reason, execution, code, message = ("pending", "none", "accepted_unverified", "pending", "Команда принята и ожидает отправки.")
                unfinished += 1
            elif status == "confirmed" and (
                evidence := _reliable_evidence(
                    request,
                    room if isinstance(room, Mapping) else {},
                    device if isinstance(device, Mapping) else {},
                    snapshot,
                    reliability_metadata.get((room_id, device_id), {})
                    if isinstance(reliability_metadata, Mapping) else {},
                )
            ) and _reliable_evidence_matches_request(evidence, request) and (
                snapshot.get("fresh") is True
                and evidence.get("fresh") is True
            ) and (
                type(dispatched_at) is int
                and dispatched_at < evidence["observed_at"] <= dispatched_at + 30_000
            ) and (
                isinstance(pre_dispatch_metadata, Mapping)
                and type(pre_dispatch_metadata.get((room_id, device_id), {}).get("source_observed_at")) is int
                and evidence["observed_at"]
                > pre_dispatch_metadata[(room_id, device_id)]["source_observed_at"]
            ):
                leaf_status, reason, execution, code, message = ("confirmed", "none", "applied", "confirmed", "Результат подтверждён чтением состояния.")
            elif status == "confirmed":
                leaf_status, reason, execution, code, message = ("pending", "none", "accepted_unverified", "pending", "Команда принята и ожидает отправки.")
                unfinished += 1
            elif status in {"pending", "partial"} and dispatched:
                leaf_status, reason, execution, code, message = ("pending", "none", "accepted_unverified", "pending", "Команда принята и ожидает отправки.")
                unfinished += 1
            elif status in {"pending", "partial"}:
                leaf_status, reason, execution, code, message = ("pending", "none", "pending_dispatch", "pending", "Команда принята и ожидает отправки.")
                unfinished += 1
            elif status == "timed_out":
                leaf_status, reason, execution, code, message = ("failed", "command_failed", "accepted_timeout", "command_failed", "Команда не подтверждена, требуется проверка устройства.")
            elif status == "rejected":
                leaf_status, reason, execution, code, message = ("not_attempted", "configuration_error", "blocked_before_dispatch", "configuration_error", "Конфигурация устройства требует проверки.")
            else:
                leaf_status, reason, execution, code, message = ("not_attempted", "device_unavailable", "blocked_before_dispatch", "deferred_offline", "Цель сохранена и будет применена после восстановления связи.")
            evidence = _reliable_evidence(
                request, room if isinstance(room, Mapping) else {},
                device if isinstance(device, Mapping) else {}, snapshot,
                reliability_metadata.get((room_id, device_id), {})
                if isinstance(reliability_metadata, Mapping) else {},
            )
            leaf: dict[str, object] = {"status": leaf_status, "reason": reason, "execution_state": execution, "message_code": code, "message": message}
            if isinstance(execution_leaf, Mapping):
                for key in ("command_count", "accepted_count", "retry_policy"):
                    if key in execution_leaf:
                        leaf[key] = execution_leaf[key]
            if execution in {"accepted_timeout"}:
                leaf.update(retry_policy="forbidden_after_dispatch", command_count=1, accepted_count=1)
            elif execution == "accepted_unverified":
                leaf.update(retry_policy="forbidden_after_dispatch", command_count=1, accepted_count=1)
            elif leaf_status == "confirmed":
                leaf.update(command_count=1, accepted_count=1)
                leaf["evidence"] = evidence
            leaves[device_id] = leaf
        statuses = {leaf["status"] for leaf in leaves.values()}
        if statuses == {"confirmed"}:
            room_status, reason, execution, code, message = ("confirmed", "none", "applied", "confirmed", "Результат подтверждён чтением состояния.")
        elif len(statuses) > 1:
            room_status, reason, execution, code, message = ("partial", "none", None, "partial", "Результаты устройств различаются.")
        else:
            first = next(iter(leaves.values()))
            room_status, reason, execution, code, message = first["status"], first["reason"], first.get("execution_state"), first["message_code"], first["message"]
            if execution == "dispatched_not_accepted":
                execution = None
        outcome = {"status": room_status, "reason": reason, "message_code": code, "message": message, "devices": leaves}
        if execution is not None:
            outcome["execution_state"] = execution
        rooms[room_id] = outcome
    return rooms, unfinished


def _reliable_evidence(
    request: ClimateTabletActionRequest, room: Mapping[str, object],
    device: Mapping[str, object], snapshot: Mapping[str, object],
    metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    desired_temperature = request.parameters.get("target_temperature")
    desired_humidity = request.parameters.get("target_humidity")
    # A room aggregate is not proof that an individual actuator reached its
    # target.  Reliable leaves publish the device's authoritative read-back.
    reported_temperature = (
        metadata.get("reported_target_temperature", device.get("reported_target_temperature"))
        if isinstance(metadata, Mapping)
        else device.get("reported_target_temperature")
    )
    reported_humidity = (
        metadata.get("reported_target_humidity", device.get("reported_target_humidity"))
        if isinstance(metadata, Mapping)
        else device.get("reported_target_humidity")
    )
    reported_minimum = room.get("minimum_temperature")
    reported_strategy = room.get("target_strategy")
    reported_mode = (
        device.get("mode")
        if request.action in {"set_room_mode", "set_device_mode"}
        else room.get("mode")
    )
    reported_state = device.get("state")
    temporary = room.get("temporary_override")
    reported_override = (
        "cleared"
        if isinstance(temporary, Mapping) and temporary.get("active") is False
        else None
    )
    participation = device.get("participation")
    reported_synchronization = (
        participation.get("synchronization")
        if isinstance(participation, Mapping)
        and participation.get("synchronization") == "in_sync"
        else None
    )
    actual = {
        "desired_target_temperature": desired_temperature, "reported_target_temperature": reported_temperature,
        "desired_target_humidity": desired_humidity, "reported_target_humidity": reported_humidity,
        "desired_minimum_temperature": request.parameters.get("minimum_temperature"), "reported_minimum_temperature": reported_minimum,
        "desired_target_strategy": request.parameters.get("target_strategy"), "reported_target_strategy": reported_strategy,
        "desired_mode": request.parameters.get("mode"), "reported_mode": reported_mode if reported_mode in {"automatic", "manual"} else None,
        "desired_state": "off" if request.action == "turn_room_off" else None, "reported_state": reported_state if reported_state == "off" else None,
        "desired_override_state": "cleared" if request.action == "clear_room_override" else None, "reported_override_state": reported_override,
        "desired_synchronization": "in_sync" if request.action == "synchronize_home" else None, "reported_synchronization": reported_synchronization,
    }
    observed_at = (
        metadata.get("source_observed_at")
        if isinstance(metadata, Mapping) else None
    )
    return {"desired_target_temperature": desired_temperature, "desired_target_humidity": desired_humidity,
            "reported_target_temperature": reported_temperature, "reported_target_humidity": reported_humidity,
            "observed_actual": actual, "observed_at": observed_at if type(observed_at) is int else 0,
            "fresh": type(observed_at) is int and snapshot.get("fresh") is True,
            "action": {"request_fingerprint": request.fingerprint, "action": request.action, "parameters": dict(request.parameters)}}


def _reliable_evidence_matches_request(
    evidence: Mapping[str, object], request: ClimateTabletActionRequest
) -> bool:
    """Require a device-level read-back for every axis the action changes."""

    actual = evidence.get("observed_actual")
    if not isinstance(actual, Mapping):
        return False
    parameters = request.parameters

    def matches(key: str) -> bool:
        desired_key = f"desired_{key}"
        reported_key = f"reported_{key}"
        expected = parameters.get(key)
        return (
            actual.get(desired_key) == expected
            and actual.get(reported_key) == expected
            and (
                key not in {"target_temperature", "target_humidity"}
                or evidence.get(reported_key) == expected
            )
        )

    if request.action == "set_home_targets":
        # A home request spans separate physical owners. A thermostat's
        # incidental room humidity is not proof of a humidity command, and a
        # humidifier's incidental room temperature is not proof of a
        # thermostat command. Native gates have already bound this leaf to
        # exactly one requested axis, so require that owner's read-back only.
        return any(
            key in parameters
            and actual.get(f"reported_{key}") is not None
            and matches(key)
            for key in ("target_temperature", "target_humidity")
        )
    if request.action == "set_room_target":
        if (
            "target_temperature" in parameters
            and actual.get("reported_target_temperature") is not None
            and not matches("target_temperature")
        ):
            return False
    if request.action == "set_room_humidity_target":
        if (
            "target_humidity" in parameters
            and actual.get("reported_target_humidity") is not None
            and not matches("target_humidity")
        ):
            return False
    if request.action == "set_room_min_target":
        return matches("minimum_temperature")
    if request.action == "set_room_target_strategy":
        return matches("target_strategy")
    if request.action in {"set_room_mode", "set_device_mode"}:
        return matches("mode")
    if request.action == "turn_room_off":
        return (
            actual.get("desired_state") == "off"
            and actual.get("reported_state") == "off"
        )
    if request.action == "clear_room_override":
        return (
            actual.get("desired_override_state") == "cleared"
            and actual.get("reported_override_state") == "cleared"
        )
    if request.action == "synchronize_home":
        return (
            actual.get("desired_synchronization") == "in_sync"
            and actual.get("reported_synchronization") == "in_sync"
        )
    return request.action in {"set_room_target", "set_room_humidity_target", "set_home_targets"}


def _validate_receipt(receipt: dict[str, object]) -> dict[str, object]:
    receipt = dict(receipt)
    if "correlation_id" not in receipt:
        receipt["correlation_id"] = receipt.get("request_id")
    required = {
        "contract", "correlation_id", "operation_id", "request_id", "action", "room_id",
        "expected_state_revision", "resulting_state_revision", "status",
        "accepted", "confirmed", "final", "duplicate", "reason", "message",
        "read_back", "created_at", "updated_at", "expires_at",
    }
    reliable_fields = {
        "action_parameters", "action_snapshot", "expected_control_revision",
        "resulting_control_revision", "unfinished_device_count", "message_code",
        "request_fingerprint", "confirmation_window_ms", "intent", "outcomes",
    }
    reliable = "action_snapshot" in receipt
    if not required <= set(receipt) or set(receipt) - required - reliable_fields:
        raise ClimateTabletUnavailable("climate operation receipt fields are invalid")
    if reliable and not reliable_fields <= set(receipt):
        raise ClimateTabletUnavailable("reliable climate operation receipt fields are invalid")
    if receipt.get("contract") != {"name": CLIMATE_OPERATION_CONTRACT_NAME, "version": 1}:
        raise ClimateTabletUnavailable("climate operation receipt contract is invalid")
    operation_id = receipt.get("operation_id")
    request_id = receipt.get("request_id")
    if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
        raise ClimateTabletUnavailable("climate operation receipt id is invalid")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise ClimateTabletUnavailable("climate operation request id is invalid")
    try:
        validate_correlation_id(receipt.get("correlation_id"))
    except ValueError as error:
        raise ClimateTabletUnavailable(
            "climate operation correlation id is invalid"
        ) from error
    if receipt.get("action") not in _ALL_ACTIONS:
        raise ClimateTabletUnavailable("climate operation action is invalid")
    room_id = receipt.get("room_id")
    if room_id is not None and (
        not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None
    ):
        raise ClimateTabletUnavailable("climate operation room is invalid")
    expected_revision = receipt.get("expected_state_revision")
    resulting_revision = receipt.get("resulting_state_revision")
    if not is_control_revision(expected_revision):
        raise ClimateTabletUnavailable("climate operation revision is invalid")
    if resulting_revision is not None and (
        not is_control_revision(resulting_revision)
    ):
        raise ClimateTabletUnavailable("climate operation result revision is invalid")
    status = receipt.get("status")
    if status not in {"pending", "confirmed", "partial", "rejected", "unavailable", "timed_out"}:
        raise ClimateTabletUnavailable("climate operation status is invalid")
    if any(
        type(receipt.get(field)) is not bool
        for field in ("accepted", "confirmed", "final", "duplicate")
    ) or receipt.get("duplicate") is not False:
        raise ClimateTabletUnavailable("climate operation flags are invalid")
    reason = receipt.get("reason")
    if reason not in _OPERATION_REASONS:
        raise ClimateTabletUnavailable("climate operation reason is invalid")
    message = receipt.get("message")
    if not isinstance(message, str) or not 1 <= len(message) <= 500:
        raise ClimateTabletUnavailable("climate operation message is invalid")
    read_back = receipt.get("read_back")
    if not isinstance(read_back, Mapping) or set(read_back) != {
        "attempted", "matched", "observed_at", "evidence"
    }:
        raise ClimateTabletUnavailable("climate operation read-back is invalid")
    if type(read_back.get("attempted")) is not bool or read_back.get("matched") not in {
        True, False, None
    }:
        raise ClimateTabletUnavailable("climate operation read-back flags are invalid")
    observed_at = read_back.get("observed_at")
    evidence = read_back.get("evidence")
    if observed_at is not None and (type(observed_at) is not int or observed_at < 0):
        raise ClimateTabletUnavailable("climate operation observation time is invalid")
    if not isinstance(evidence, Mapping) or len(evidence) > 16:
        raise ClimateTabletUnavailable("climate operation evidence is invalid")
    for field in ("created_at", "updated_at", "expires_at"):
        if type(receipt.get(field)) is not int or receipt[field] < 0:
            raise ClimateTabletUnavailable("climate operation timestamp is invalid")
    if not receipt["created_at"] <= receipt["updated_at"] <= receipt["expires_at"]:
        raise ClimateTabletUnavailable("climate operation timestamp order is invalid")
    flags = (
        receipt["accepted"],
        receipt["confirmed"],
        receipt["final"],
        reason,
        resulting_revision,
    )
    if status == "pending" and flags != (True, False, False, "none", None):
        raise ClimateTabletUnavailable("pending climate operation is inconsistent")
    if status == "confirmed" and not (
        flags[:4] == (True, True, True, "none")
        and is_control_revision(resulting_revision)
        and read_back.get("attempted") is True
        and read_back.get("matched") is True
    ):
        raise ClimateTabletUnavailable("confirmed climate operation is inconsistent")
    if status in {"rejected", "unavailable"} and not (
        flags[0:3] == (False, False, True)
        and reason != "none"
        and resulting_revision is None
    ):
        raise ClimateTabletUnavailable("failed climate operation is inconsistent")
    if status == "timed_out" and not (
        flags[0:3] == (True, False, True) and reason != "none"
    ):
        raise ClimateTabletUnavailable("incomplete climate operation is inconsistent")
    if status == "partial" and not (
        flags[0:2] == (True, False) and reason != "none"
    ):
        raise ClimateTabletUnavailable("partial climate operation is inconsistent")
    return dict(receipt)


def _operation_summary(receipt: Mapping[str, object]) -> dict[str, object]:
    return {
        "operation_id": receipt["operation_id"],
        "request_id": receipt["request_id"],
        "action": receipt["action"],
        "room_id": receipt["room_id"],
        "status": receipt["status"],
        "updated_at": receipt["updated_at"],
    }


def _climate_mode(runtime: ClimateTabletRuntime) -> str:
    configuration = getattr(runtime, "configuration", None)
    bridge_mode = getattr(
        getattr(configuration, "climate_bridge_mode", None), "value", None
    )
    if bridge_mode == "managed":
        return "managed"
    return "shadow" if getattr(configuration, "mode", None) == "shadow" else "disabled"


def _require_room(room_id: object) -> None:
    if not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None:
        raise ClimateTabletViolation("climate room is required")


def _validate_temperature(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClimateTabletViolation("climate temperature is invalid")
    number = float(value)
    if not 18 <= number <= 28 or round(number * 2) != number * 2:
        raise ClimateTabletViolation("climate temperature is invalid")


def _validate_humidity(value: object) -> None:
    if type(value) is not int or not 30 <= value <= 70:
        raise ClimateTabletViolation("climate humidity is invalid")


def _canonical_fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _is_legacy_tablet_fingerprint(
    payload_version: object,
    request: ClimateTabletActionRequest,
    fingerprint: str,
) -> bool:
    """Recognize only the pre-reliability canonical operation identity."""

    if (
        type(payload_version) is not int
        or not 1 <= payload_version <= 5
        or request.reliability_profile is not None
    ):
        return False
    return fingerprint == _canonical_fingerprint({
        "expected_state_revision": request.expected_state_revision,
        "action": request.action,
        "room_id": request.room_id,
        "parameters": request.parameters,
    })


def _recovery_request_fingerprint(request: Mapping[str, object]) -> str:
    """Fingerprint the recovery command, not its transport envelope."""
    return _canonical_fingerprint({
        key: value for key, value in request.items()
        if key not in {"request_id", "correlation_id", "request_fingerprint"}
    })


def _intent_key(request: ClimateTabletActionRequest) -> str:
    if request.action == "set_home_targets":
        return "home"
    if request.action == "set_device_mode":
        return f"device:{request.room_id}:{request.parameters['device_id']}"
    return f"room:{request.room_id}" if request.room_id is not None else "home"


def _intent_baseline(snapshot: Mapping[str, object], request: ClimateTabletActionRequest) -> dict[str, object]:
    """Freeze the desired room state before applying one validated delta.

    Reported runtime values are deliberately only a bootstrap when no durable
    desired intent exists.  Every later recovery reads this stored snapshot,
    including after a process restart.
    """
    rooms = snapshot.get("rooms")
    room = next((item for item in rooms if isinstance(item, Mapping) and item.get("id") == request.room_id), None) if isinstance(rooms, list) else None
    if not isinstance(room, Mapping):
        return {}
    return {
        "target_temperature": room.get("desired_target_temperature", room.get("target_temperature")),
        "target_humidity": room.get("desired_target_humidity", room.get("target_humidity")),
        "minimum_temperature": room.get("desired_minimum_temperature", room.get("minimum_temperature")),
        "target_strategy": room.get("desired_target_strategy", room.get("target_strategy")),
        "mode": room.get("desired_mode", room.get("mode", "automatic")),
        "override_state": room.get("desired_override_state", "none"),
    }


def _desired_intent(request: ClimateTabletActionRequest, control_revision: int,
                    baseline: Mapping[str, object] | None = None) -> dict[str, object]:
    """Persist only server-validated action values, never the raw client body."""
    values = {
        "target_temperature": None,
        "target_humidity": None,
        "minimum_temperature": None,
        "target_strategy": None,
        "mode": None,
        "override_state": None,
    }
    # Merge only a previously durable intent for this exact scope.  Runtime
    # observations are never a baseline: otherwise a humidity patch could
    # silently overwrite an earlier temperature choice after restart.
    if isinstance(baseline, Mapping):
        previous = baseline.get("parameters")
        if isinstance(previous, Mapping):
            for key in values:
                if key in previous:
                    values[key] = previous[key]
    if request.action == "set_room_target":
        values.update(target_temperature=request.parameters["target_temperature"], override_state="active")
    elif request.action == "clear_room_override":
        values["override_state"] = "cleared"
    elif request.action == "set_room_humidity_target":
        values["target_humidity"] = request.parameters["target_humidity"]
    elif request.action == "set_room_min_target":
        values["minimum_temperature"] = request.parameters["minimum_temperature"]
    elif request.action == "set_room_target_strategy":
        values["target_strategy"] = request.parameters["target_strategy"]
    elif request.action in {"set_room_mode", "set_device_mode"}:
        values["mode"] = request.parameters["mode"]
    elif request.action == "set_home_targets":
        values.update({key: value for key, value in request.parameters.items() if key in values})
    result = {
        "scope_key": _intent_key(request),
        "origin_request_id": request.request_id,
        "control_revision": control_revision,
        "action": request.action,
        "room_id": request.room_id,
        "parameters": values,
        "request_fingerprint": request.fingerprint,
    }
    result["intent_fingerprint"] = _canonical_fingerprint(result)
    return result


def _validate_desired_intent(key: str, value: Mapping[str, object]) -> dict[str, object]:
    """Reject damaged desired state before it can become a recovery command."""
    if not (key == "home" or re.fullmatch(r"room:[a-z][a-z0-9_-]{0,63}", key)
            or re.fullmatch(r"device:[a-z][a-z0-9_-]{0,63}:[a-z][a-z0-9_-]{0,63}", key)):
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    if set(value) != {
        "scope_key", "control_revision", "action", "room_id", "parameters",
        "request_fingerprint", "origin_request_id", "intent_fingerprint",
    }:
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    scope_key = value.get("scope_key")
    revision = value.get("control_revision")
    action = value.get("action")
    room_id = value.get("room_id")
    fingerprint = value.get("request_fingerprint")
    origin_request_id = value.get("origin_request_id")
    intent_fingerprint = value.get("intent_fingerprint")
    parameters = value.get("parameters")
    if (scope_key != key
            or not is_control_revision(revision) or action not in _SUPPORTED_ACTIONS
            or room_id is not None and (not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None)
            or not isinstance(fingerprint, str) or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
            or not isinstance(origin_request_id, str) or _REQUEST_ID.fullmatch(origin_request_id) is None
            or not isinstance(intent_fingerprint, str) or re.fullmatch(r"[a-f0-9]{64}", intent_fingerprint) is None
            or not isinstance(parameters, Mapping)
            or set(parameters) != {"target_temperature", "target_humidity", "minimum_temperature", "target_strategy", "mode", "override_state"}):
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    result = dict(value)
    if result["intent_fingerprint"] != _canonical_fingerprint({
        key: item for key, item in result.items() if key != "intent_fingerprint"
    }):
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    normalized = dict(parameters)
    for field in ("target_temperature", "minimum_temperature"):
        candidate = normalized[field]
        if candidate is not None:
            try:
                _validate_temperature(candidate)
            except ClimateTabletViolation as error:
                raise ClimateTabletUnavailable("stored climate desired intent is invalid") from error
    if normalized["target_humidity"] is not None:
        try:
            _validate_humidity(normalized["target_humidity"])
        except ClimateTabletViolation as error:
            raise ClimateTabletUnavailable("stored climate desired intent is invalid") from error
    if normalized["target_strategy"] not in {None, "soft", "normal", "aggressive"}:
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    if normalized["mode"] not in {None, "automatic", "manual"}:
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    if normalized["override_state"] not in {None, "none", "active", "cleared"}:
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    required_axis = {
        "set_room_target": "target_temperature",
        "set_room_humidity_target": "target_humidity",
        "set_room_min_target": "minimum_temperature",
        "set_room_target_strategy": "target_strategy",
        "set_room_mode": "mode",
        "set_device_mode": "mode",
        "clear_room_override": "override_state",
    }.get(action)
    if required_axis is not None and normalized[required_axis] is None:
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    if action == "set_home_targets" and (
        normalized["target_temperature"] is None
        and normalized["target_humidity"] is None
    ):
        raise ClimateTabletUnavailable("stored climate desired intent is invalid")
    result["parameters"] = normalized
    return result


def _reliability_metadata(home: Mapping[str, object]) -> dict[tuple[str, str], dict[str, object]]:
    """Keep command provenance out of the public v1 runtime representation."""
    result: dict[tuple[str, str], dict[str, object]] = {}
    rooms = home.get("rooms")
    if not isinstance(rooms, list):
        return result
    for room in rooms:
        if not isinstance(room, Mapping) or not isinstance(room.get("id"), str):
            continue
        devices = room.get("devices")
        if not isinstance(devices, list):
            continue
        for device in devices:
            if not isinstance(device, Mapping) or not isinstance(device.get("id"), str):
                continue
            result[(room["id"], device["id"])] = {
                "manual_reason": device.get("manual_reason"),
                "source_observed_at": device.get("observed_at"),
                "reported_target_temperature": device.get(
                    "reported_target_temperature", device.get("target_temperature")
                ),
                "reported_target_humidity": device.get(
                    "reported_target_humidity", device.get("target_humidity")
                ),
            }
    return result


def _validate_private_recovery_metadata(value: Mapping[str, object]) -> dict[str, object]:
    """Accept only bounded private proof fields from the runtime boundary."""
    allowed = {
        "manual_reason", "source_observed_at", "reported_target_temperature",
        "reported_target_humidity",
    }
    if not set(value) <= allowed:
        raise ClimateTabletUnavailable("climate recovery proof is invalid")
    source = value.get("source_observed_at")
    if "source_observed_at" in value and (
        source is not None
        and (type(source) is not int or not 0 <= source <= 9_007_199_254_740_991)
    ):
        raise ClimateTabletUnavailable("climate recovery proof is invalid")
    manual_reason = value.get("manual_reason")
    if "manual_reason" in value and manual_reason not in {
        None, "user_excluded", "external_off",
    }:
        raise ClimateTabletUnavailable("climate recovery proof is invalid")
    for key, lower, upper in (
        ("reported_target_temperature", 10.0, 35.0),
        ("reported_target_humidity", 0.0, 100.0),
    ):
        candidate = value.get(key)
        if key in value and candidate is not None:
            if type(candidate) not in {int, float} or not lower <= candidate <= upper:
                raise ClimateTabletUnavailable("climate recovery proof is invalid")
    return dict(value)


def _with_reliability_projection(
    snapshot: dict[str, object],
    intents: Mapping[str, Mapping[str, object]],
    control_revision: int,
    metadata: Mapping[tuple[str, str], Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Attach negotiated fields without changing the legacy representation."""
    rooms = snapshot.get("rooms")
    if not isinstance(rooms, list):
        return snapshot
    counts = {"automatic": 0, "manual": 0, "deferred": 0, "unavailable": 0}
    for room in rooms:
        if not isinstance(room, dict) or not isinstance(room.get("id"), str):
            continue
        room_id = room["id"]
        intent = intents.get(f"room:{room_id}", intents.get("home", {}))
        parameters = intent.get("parameters", {}) if isinstance(intent, Mapping) else {}
        if not isinstance(parameters, Mapping):
            parameters = {}
        def desired(key: str, fallback: object) -> object:
            # All persisted axes are explicit.  Keep null exactly as stored
            # rather than replacing it with a new observation at projection
            # time.  Mode is the ownership baseline, not a target axis.
            if key in parameters:
                value = parameters.get(key)
                if value is not None or key != "mode":
                    return value
            return fallback

        room["control_revision"] = control_revision
        room["desired_target_temperature"] = desired("target_temperature", room.get("target_temperature"))
        room["reported_target_temperature"] = room.get("target_temperature")
        room["desired_target_humidity"] = desired("target_humidity", room.get("target_humidity"))
        room["reported_target_humidity"] = room.get("target_humidity")
        room["desired_minimum_temperature"] = desired("minimum_temperature", room.get("minimum_temperature"))
        room["desired_target_strategy"] = desired("target_strategy", room.get("target_strategy"))
        room["desired_mode"] = desired("mode", room.get("mode", "automatic"))
        room["desired_override_state"] = desired("override_state", "none")
        devices = room.get("devices")
        if not isinstance(devices, list):
            continue
        eligible: list[str] = []
        for device in devices:
            if not isinstance(device, dict) or not isinstance(device.get("id"), str):
                continue
            device_id = device["id"]
            device_intent = intents.get(f"device:{room_id}:{device_id}", intent)
            device_parameters = device_intent.get("parameters", {}) if isinstance(device_intent, Mapping) else {}
            mode = device.get("mode") if device.get("mode") in {"automatic", "manual"} else "automatic"
            available = device.get("available") is True
            source = metadata.get((room_id, device_id), {}) if metadata is not None else device
            desired_temperature = (
                device_parameters["target_temperature"]
                if "target_temperature" in device_parameters
                else room["desired_target_temperature"]
            )
            reported_temperature = source.get(
                "reported_target_temperature", device.get("target_temperature")
            )
            desired_humidity = (
                device_parameters["target_humidity"]
                if "target_humidity" in device_parameters
                else room["desired_target_humidity"]
            )
            reported_humidity = source.get(
                "reported_target_humidity", device.get("target_humidity")
            )
            desired_mode = (
                device_parameters.get("mode")
                if device_parameters.get("mode") in {"automatic", "manual"}
                else room["desired_mode"]
            )
            synchronized = available and all(
                expected is None or reported == expected
                for expected, reported in (
                    (desired_temperature, reported_temperature),
                    (desired_humidity, reported_humidity),
                    (desired_mode, mode),
                )
            )
            manual_reason = source.get("manual_reason") if mode == "manual" else None
            # Missing provenance is an unknown/manual state, not permission
            # to return the leaf to the contour.
            user_excluded = manual_reason == "user_excluded"
            recoverable_manual = manual_reason in {"user_excluded", "external_off"}
            participation = {
                "mode": mode, "connectivity": "available" if available else "unavailable",
                "synchronization": (
                    "in_sync" if synchronized else ("pending" if available else "deferred")
                ),
                "reason": (("user_excluded" if user_excluded else manual_reason) if mode == "manual" else ("none" if available else "device_unavailable")),
                "changed_at": (
                    device.get("participation_changed_at")
                    if type(device.get("participation_changed_at")) is int
                    else snapshot.get("generated_at", 0)
                ),
                "message_code": ("manual_excluded" if user_excluded else "external_off") if mode == "manual" else ("in_sync" if synchronized else ("pending" if available else "device_unavailable")),
                "message": ("Устройство исключено пользователем из автоматического контура." if user_excluded else "Устройство выключено внешней командой.") if mode == "manual" else ("Результат подтверждён чтением состояния." if synchronized else ("Подтверждение состояния устройства ожидается." if available else "Цель сохранена и будет применена после восстановления связи.")),
                "recovery": "return_to_contour" if mode == "manual" and recoverable_manual else ("none" if available else "wait_for_connection"),
            }
            device["control_revision"] = control_revision
            device["participation"] = participation
            device["desired_target_temperature"] = desired_temperature
            device["reported_target_temperature"] = reported_temperature
            device["desired_target_humidity"] = desired_humidity
            device["reported_target_humidity"] = reported_humidity
            device["desired_mode"] = desired_mode
            # These are independent dimensions.  An offline manually
            # excluded leaf remains excluded and is also unavailable; using a
            # mutually exclusive bucket silently lost ownership on restart.
            counts[mode] += 1
            if not available:
                counts["unavailable"] += 1
            if participation["synchronization"] in {"deferred", "pending"}:
                counts["deferred"] += 1
            # Recovery is a one-step return from either explicitly recorded
            # manual reason.  An automatic or unknown device is never a
            # recovery candidate.
            if mode == "manual" and recoverable_manual:
                eligible.append(device_id)
    snapshot["participation_summary"] = {
        "automatic_count": counts["automatic"], "excluded_count": counts["manual"],
        "unavailable_count": counts["unavailable"], "pending_sync_count": counts["deferred"],
    }
    return snapshot


def _recovery_preflight(snapshot: Mapping[str, object], room_id: str) -> dict[str, object]:
    """Derive recovery scope only from the current authoritative projection."""
    rooms = snapshot.get("rooms")
    room = next((item for item in rooms if isinstance(item, Mapping) and item.get("id") == room_id), None) if isinstance(rooms, list) else None
    if (
        not isinstance(room, Mapping)
        or snapshot.get("phase") != "managed"
        or snapshot.get("authority") != "hausman_hub"
        or snapshot.get("fresh") is not True
        or snapshot.get("commands_enabled") is not True
        or snapshot.get("blocked_reasons") not in ([], ())
        or not isinstance(room.get("control"), Mapping)
        or room["control"].get("enabled") is not True
        or room["control"].get("blocked_reasons") not in ([], ())
    ):
        raise ClimateTabletViolation("recovery preflight is unavailable", code="action_unsupported")
    devices = room.get("devices")
    if not isinstance(devices, list):
        raise ClimateTabletViolation("recovery scope is invalid", code="action_unsupported")
    desired: dict[str, dict[str, object]] = {}
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        device_id = device.get("id")
        if isinstance(device_id, str) and _STABLE_ID.fullmatch(device_id):
            desired_mode = device.get("desired_mode", room.get("desired_mode", "automatic"))
            participation = device.get("participation")
            control = device.get("control")
            if (device.get("control_scope") != "managed"
                    or device.get("kind") not in {
                        "air_conditioner", "humidifier", "radiator_thermostat",
                    }
                    or device.get("mode") != "manual"
                    or desired_mode != "automatic"
                    or not isinstance(control, Mapping)
                    or control.get("enabled") is not True
                    or not isinstance(control.get("allowed_actions"), list)
                    or "set_device_mode" not in control["allowed_actions"]
                    or control.get("blocked_reasons") not in ([], ())
                    or not isinstance(participation, Mapping)
                    or participation.get("reason") not in {"user_excluded", "external_off"}
                    or participation.get("recovery") != "return_to_contour"):
                continue
            desired[device_id] = {
                "target_temperature": device.get("desired_target_temperature"),
                "target_humidity": device.get("desired_target_humidity"),
                "mode": desired_mode,
            }
    if not desired:
        raise ClimateTabletViolation("recovery has no eligible devices", code="action_unsupported")
    control_revision = snapshot.get("control_revision", snapshot.get("state_revision"))
    if not is_control_revision(control_revision):
        raise ClimateTabletViolation("recovery revision is invalid", code="action_unsupported")
    scope = {"room_id": room_id, "control_revision": control_revision,
             "resolved_device_ids": sorted(desired), "desired_snapshot": desired}
    fingerprint = _canonical_fingerprint(scope)
    available_ids = sorted(
        device_id for device_id in desired
        if any(isinstance(item, Mapping) and item.get("id") == device_id and item.get("available") is True for item in devices)
    )
    return {**scope, "available_device_ids": available_ids, "preflight_snapshot_fingerprint": fingerprint,
            "snapshot_token": f"recovery.{room_id}.{control_revision}.{fingerprint[:24]}"}


def _recovery_device_preflight(snapshot: Mapping[str, object], room_id: str, device_id: str) -> dict[str, object]:
    """Recheck a single recovery leaf immediately before its HA call."""

    preflight = _recovery_preflight(snapshot, room_id)
    if device_id not in preflight["resolved_device_ids"]:
        raise ClimateTabletViolation("recovery device gate changed", code="action_unsupported")
    return preflight


def _parse_recovery_request(payload: Mapping[str, object], room_id: str, preflight: Mapping[str, object]) -> dict[str, object]:
    required = {"contract", "request_id", "request_fingerprint", "expected_control_revision",
                "expected_desired_snapshot_fingerprint", "expected_resolved_device_ids", "snapshot_token", "reliability_profile"}
    if not required <= set(payload) <= required | {"device_ids", "correlation_id"}:
        raise ClimateTabletViolation("climate recovery request fields are invalid")
    if payload.get("contract") != {"name": "hausman-hub-climate-room-recovery-request-v2", "version": 2} or payload.get("reliability_profile") != "climate_recovery_proof_v1":
        raise ClimateTabletViolation("climate recovery contract is invalid")
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise ClimateTabletViolation("climate recovery request id is invalid")
    ids = payload.get("expected_resolved_device_ids")
    if not isinstance(ids, list) or ids != sorted(preflight["resolved_device_ids"]):
        raise ClimateTabletViolation("climate recovery scope changed", code="revision_conflict")
    if (payload.get("expected_control_revision") != preflight["control_revision"]
            or payload.get("expected_desired_snapshot_fingerprint") != preflight["preflight_snapshot_fingerprint"]
            or payload.get("snapshot_token") != preflight["snapshot_token"]):
        raise ClimateTabletViolation("climate recovery preflight changed", code="revision_conflict")
    # Omitted selection means the room action "return all available".  An
    # explicit subset may intentionally return an offline leaf to automatic
    # ownership without attempting a physical call.
    selected = payload.get("device_ids", preflight.get("available_device_ids", preflight["resolved_device_ids"]))
    # Validate the externally supplied leaf list before sorting or making a
    # set from it.  In particular, JSON values such as 1 must produce the
    # normal request error, never leak a TypeError from mixed sorting.
    if (
        not isinstance(selected, list)
        or not (0 <= len(selected) <= 32 and ("device_ids" not in payload or len(selected) > 0))
        or any(not isinstance(device_id, str) or _STABLE_ID.fullmatch(device_id) is None
               for device_id in selected)
        or len(set(selected)) != len(selected)
        or selected != sorted(selected)
        or not set(selected) <= set(preflight["resolved_device_ids"])
    ):
        raise ClimateTabletViolation("climate recovery selected scope is invalid")
    correlation = payload.get("correlation_id", request_id)
    if not isinstance(correlation, str) or _REQUEST_ID.fullmatch(correlation) is None:
        raise ClimateTabletViolation("climate recovery correlation id is invalid")
    request_copy = dict(payload)
    expected_fingerprint = _recovery_request_fingerprint(request_copy)
    if payload.get("request_fingerprint") != expected_fingerprint:
        raise ClimateTabletViolation("climate recovery fingerprint is invalid")
    return {"request_id": request_id, "correlation_id": correlation, "correlation_policy": "supplied" if "correlation_id" in payload else "generated",
            "request_fingerprint": expected_fingerprint, "request": request_copy,
        "selected_device_ids": selected, "room_id": room_id}


def _recovery_leaf(state: str, *, dispatched_at: int | None = None) -> dict[str, object]:
    """Return one canonical, persisted recovery-leaf transition."""
    table = {
        "pending_dispatch": {"status": "pending", "reason": "none", "execution_state": state,
                             "retry_policy": "forbidden_after_dispatch", "command_count": 0, "accepted_count": 0,
                             "message_code": "pending", "message": "Команда принята и ожидает подтверждения."},
        # `ledger_state` is intentionally private storage metadata.  The
        # negotiated outcome vocabulary has no started leaf, so its public
        # projection remains 0/0 pending while the operation itself reports
        # an unknown, non-replayable `started` dispatch boundary.
        "started": {"ledger_state": "started", "status": "pending", "reason": "none", "execution_state": "pending_dispatch",
                    "retry_policy": "forbidden_after_dispatch", "command_count": 0, "accepted_count": 0,
                    "message_code": "pending", "message": "Команда принята и ожидает подтверждения."},
        "accepted_unverified": {"status": "pending", "reason": "none", "execution_state": state,
                                  "retry_policy": "forbidden_after_dispatch", "command_count": 1, "accepted_count": 1,
                                  "message_code": "pending", "message": "Команда принята и ожидает подтверждения."},
        "applied": {"status": "confirmed", "reason": "none", "execution_state": state,
                    "command_count": 1, "accepted_count": 1, "message_code": "confirmed",
                    "message": "Результат подтверждён чтением состояния."},
        "dispatched_not_accepted": {"status": "failed", "reason": "command_failed", "execution_state": state,
                                     "retry_policy": "forbidden_after_dispatch", "command_count": 1, "accepted_count": 0,
                                     "message_code": "command_failed", "message": "Команда не подтверждена, требуется проверка устройства."},
        "blocked_before_dispatch": {"status": "not_attempted", "reason": "configuration_error", "execution_state": state,
                                     "retry_policy": "allowed_pre_dispatch_only", "command_count": 0, "accepted_count": 0,
                                     "message_code": "configuration_error", "message": "Конфигурация устройства требует проверки."},
        "deferred_offline": {"ledger_state": "deferred_offline", "status": "deferred", "reason": "device_unavailable",
                             "command_count": 0, "accepted_count": 0,
                             "message_code": "deferred_offline", "message": "Цель сохранена и будет применена после восстановления связи."},
    }
    result = dict(table[state])
    result["dispatched_at"] = None
    if dispatched_at is not None:
        result["dispatched_at"] = dispatched_at
    return result


def _valid_recovery_ledger(ledger: object, receipt: Mapping[str, object]) -> bool:
    if not isinstance(ledger, Mapping) or set(ledger) != set(receipt.get("resolved_device_ids", [])):
        return False
    return all(isinstance(key, str) and isinstance(value, Mapping)
        and value.get("ledger_state", value.get("execution_state")) in {"pending_dispatch", "started", "accepted_unverified", "applied", "dispatched_not_accepted", "blocked_before_dispatch", "deferred_offline"}
               for key, value in ledger.items())


def _valid_recovery_record(
    receipt: Mapping[str, object], ledger: Mapping[str, object], preflight: Mapping[str, object],
) -> bool:
    """Verify the durable v2 receipt is exactly the projection of its ledger."""

    token = preflight.get("snapshot_token")
    if not _valid_recovery_preflight_record({
        "token": token, "preflight": preflight, "expires_at": 0,
    }):
        return False
    if (
        receipt.get("contract") != {
            "name": "hausman-hub-climate-room-recovery-receipt-v2", "version": 2,
        }
        or not isinstance(receipt.get("operation_id"), str)
        or _OPERATION_ID.fullmatch(receipt["operation_id"]) is None
        or type(receipt.get("created_at")) is not int
        or receipt["created_at"] < 0
    ):
        return False
    snapshot = receipt.get("request_snapshot")
    frozen_request = snapshot.get("request") if isinstance(snapshot, Mapping) else None
    if not isinstance(frozen_request, Mapping):
        return False
    try:
        request = _parse_recovery_request(
            frozen_request, receipt.get("room_id"), preflight
        )
    except (ClimateTabletViolation, TypeError):
        return False
    if (
        request["request_id"] != receipt.get("request_id")
        or request["request_fingerprint"] != receipt.get("request_fingerprint")
        or request["correlation_id"] != receipt.get("correlation_id")
        or receipt.get("request_snapshot", {}).get("path_room_id") != request["room_id"]
    ):
        return False
    for device_id, leaf in ledger.items():
        if not isinstance(device_id, str) or not isinstance(leaf, Mapping):
            return False
        state = leaf.get("ledger_state", leaf.get("execution_state"))
        dispatched_at = leaf.get("dispatched_at")
        if (
            state not in {
                "pending_dispatch", "started", "accepted_unverified", "applied",
                "dispatched_not_accepted", "blocked_before_dispatch", "deferred_offline",
            }
            or (dispatched_at is not None and (
                type(dispatched_at) is not int or dispatched_at < 0
            ))
            or dict(leaf) != _recovery_leaf(state, dispatched_at=dispatched_at)
        ):
            return False
    observed_at = _recovery_receipt_observed_at(receipt)
    for device_id, leaf in ledger.items():
        if not isinstance(leaf, Mapping):
            return False
        state = leaf.get("ledger_state", leaf.get("execution_state"))
        evidence_time = observed_at.get(device_id)
        if state != "applied":
            if evidence_time is not None:
                return False
            continue
        desired = preflight.get("desired_snapshot", {}).get(device_id)
        dispatched_at = leaf.get("dispatched_at")
        source_observed_at = (
            desired.get("source_observed_at") if isinstance(desired, Mapping) else None
        )
        if (
            type(dispatched_at) is not int
            or type(source_observed_at) is not int
            or type(evidence_time) is not int
            or evidence_time <= dispatched_at
            or evidence_time <= source_observed_at
            or evidence_time > dispatched_at + 30_000
        ):
            return False
    expected = _recovery_receipt(
        request, preflight, receipt["operation_id"], receipt["created_at"], ledger,
        observed_at_by_device=observed_at,
    )
    return dict(receipt) == expected


def _recovery_ledger_lacks_dispatch_evidence(ledger: Mapping[str, object]) -> bool:
    """Identify only legacy in-flight leaves that lack a physical boundary."""
    for leaf in ledger.values():
        if not isinstance(leaf, Mapping):
            return True
        state = leaf.get("ledger_state", leaf.get("execution_state"))
        if state in {"started", "accepted_unverified"} and type(leaf.get("dispatched_at")) is not int:
            return True
    return False


def _freeze_unproven_recovery_receipt(receipt: Mapping[str, object], ledger: Mapping[str, object]) -> dict[str, object]:
    """Freeze an unreconstructable v2 recovery without any redispatch."""
    result = dict(receipt)
    selected = result.get("resolved_device_ids")
    ids = selected if isinstance(selected, list) else list(ledger)
    result["outcomes"] = {
        device_id: {key: value for key, value in leaf.items() if key != "ledger_state"}
        for device_id, leaf in ledger.items() if device_id in ids and isinstance(leaf, Mapping)
    }
    result.update(
        status="unknown", accepted=True, confirmed=False, final=False,
        duplicate=False, dispatch_state="started",
        retry_policy="forbidden_after_dispatch", message_code="unknown",
        message="Результат восстановления пока неизвестен.",
        read_back={"attempted": False, "matched": None, "observed_at": None, "evidence": {}},
        updated_at=result.get("created_at"),
    )
    return result


def _valid_recovery_preflight_record(item: object) -> bool:
    """Fail closed when a persisted v2 authority token is malformed."""
    if not isinstance(item, Mapping) or set(item) != {"token", "preflight", "expires_at"}:
        return False
    token = item.get("token")
    preflight = item.get("preflight")
    expires_at = item.get("expires_at")
    if (not isinstance(token, str) or re.fullmatch(r"recovery\.v2\.[a-f0-9]{32}", token) is None
            or not isinstance(preflight, Mapping) or type(expires_at) is not int or expires_at < 0):
        return False
    required = {"room_id", "control_revision", "resolved_device_ids", "desired_snapshot", "available_device_ids", "preflight_snapshot_fingerprint", "snapshot_token"}
    if set(preflight) != required or preflight.get("snapshot_token") != token:
        return False
    room_id = preflight.get("room_id")
    revision = preflight.get("control_revision")
    ids = preflight.get("resolved_device_ids")
    available = preflight.get("available_device_ids")
    desired = preflight.get("desired_snapshot")
    if (not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None
            or not is_control_revision(revision)
            or not isinstance(ids, list) or not 1 <= len(ids) <= 32
            or any(not isinstance(device_id, str) or _STABLE_ID.fullmatch(device_id) is None for device_id in ids)
            or ids != sorted(ids) or len(set(ids)) != len(ids)
            or not isinstance(available, list) or len(available) > len(ids)
            or any(not isinstance(device_id, str) or _STABLE_ID.fullmatch(device_id) is None for device_id in available)
            or available != sorted(available) or len(set(available)) != len(available)
            or not set(available) <= set(ids)
            or not isinstance(desired, Mapping) or set(desired) != set(ids)):
        return False
    for device_id, value in desired.items():
        if (not isinstance(device_id, str) or _STABLE_ID.fullmatch(device_id) is None
                or not isinstance(value, Mapping)
                or set(value) != {"target_temperature", "target_humidity", "mode", "source_observed_at"}):
            return False
        try:
            if value["target_temperature"] is not None:
                _validate_temperature(value["target_temperature"])
            if value["target_humidity"] is not None:
                _validate_humidity(value["target_humidity"])
        except ClimateTabletViolation:
            return False
        if (value.get("mode") != "automatic"
                or (value.get("source_observed_at") is not None
                    and (type(value["source_observed_at"]) is not int
                         or not 0 <= value["source_observed_at"] <= 9_007_199_254_740_991))
                or (device_id in available
                    and (type(value.get("source_observed_at")) is not int
                         or not 0 <= value["source_observed_at"] <= 9_007_199_254_740_991))):
            return False
    scope = {"room_id": room_id, "control_revision": revision,
             "resolved_device_ids": ids, "desired_snapshot": desired}
    return preflight.get("preflight_snapshot_fingerprint") == _canonical_fingerprint(scope)


def _recovery_unresolved_device_ids(ledger: Mapping[str, object]) -> set[str]:
    """Return leaves that cannot be safely superseded or dispatched again."""
    return {
        device_id for device_id, leaf in ledger.items()
        if isinstance(device_id, str) and isinstance(leaf, Mapping)
        and leaf.get("ledger_state", leaf.get("execution_state"))
        in {"pending_dispatch", "started", "accepted_unverified", "dispatched_not_accepted"}
    }


def _recovery_receipt(request: Mapping[str, object], preflight: Mapping[str, object], operation_id: str,
                      now: int, ledger: Mapping[str, Mapping[str, object]], *,
                      observed_at_by_device: Mapping[str, int] | None = None) -> dict[str, object]:
    selected = request["selected_device_ids"]
    desired = {device_id: preflight["desired_snapshot"][device_id] for device_id in selected}
    desired_fingerprint = _canonical_fingerprint(desired)
    outcomes = {device_id: {key: value for key, value in ledger[device_id].items() if key != "ledger_state"}
                for device_id in selected}
    if not selected:
        return {"contract": {"name": "hausman-hub-climate-room-recovery-receipt-v2", "version": 2},
            "correlation_id": request["correlation_id"], "operation_id": operation_id, "request_id": request["request_id"], "request_fingerprint": request["request_fingerprint"],
            "request_snapshot": {"path_room_id": request["room_id"], "request": request["request"], "correlation_policy": request["correlation_policy"], "confirmation_window_ms": 30000, "reliability_profile": "climate_reliability_v1"},
            "room_id": request["room_id"], "expected_control_revision": preflight["control_revision"], "resulting_control_revision": preflight["control_revision"] + 1, "desired_snapshot_revision": preflight["control_revision"], "preflight_snapshot_fingerprint": preflight["preflight_snapshot_fingerprint"], "desired_snapshot_fingerprint": _canonical_fingerprint({}), "snapshot_token": preflight["snapshot_token"], "resolved_device_ids": [], "desired_snapshot": {}, "status": "partial", "accepted": True, "confirmed": False, "final": True, "duplicate": False, "dispatch_state": "accepted", "retry_policy": "forbidden_after_dispatch", "confirmation_window_ms": 30000, "read_back": {"attempted": False, "matched": None, "observed_at": None, "evidence": {}}, "message_code": "partial", "message": "Восстановление завершено частично.", "outcomes": {}, "created_at": now, "updated_at": now}
    states = {item.get("ledger_state", item.get("execution_state")) for item in ledger.values()}
    all_applied = states == {"applied"}
    any_applied = "applied" in states
    any_dispatched_failure = "dispatched_not_accepted" in states
    any_started = "started" in states or "accepted_unverified" in states
    any_pending = "pending_dispatch" in states
    any_deferred = any(
        outcome.get("status") == "deferred"
        for outcome in outcomes.values()
    )
    if any_started:
        # A received command is never terminal until every dispatched leaf
        # has authoritative post-dispatch evidence.  Confirmed siblings are
        # retained in the same durable unknown receipt.
        status, accepted, confirmed, final, dispatch_state = (
            "unknown", True, False, False,
            "started" if "started" in states else "accepted_unverified",
        )
    elif all_applied:
        status, accepted, confirmed, final, dispatch_state = "confirmed", True, True, True, "accepted"
    elif any_applied or any_deferred:
        status, accepted, confirmed, final, dispatch_state = "partial", True, False, True, "mixed"
    elif any_dispatched_failure and not (any_started or any_pending):
        status, accepted, confirmed, final, dispatch_state = "failed", False, False, True, "dispatched_not_accepted"
    elif any_pending:
        status, accepted, confirmed, final, dispatch_state = "pending", True, False, False, "not_started"
    else:
        status, accepted, confirmed, final, dispatch_state = "failed", False, False, True, "pre_dispatch_failed"
    evidence = {
        device_id: {
            "action": "return_to_contour",
            "mode": "automatic",
            "target_temperature": desired[device_id]["target_temperature"],
            "target_humidity": desired[device_id]["target_humidity"],
            "observed_at": observed_at_by_device[device_id],
            "fresh": True,
        }
        for device_id, outcome in outcomes.items()
        if outcome.get("execution_state") == "applied"
        and isinstance(observed_at_by_device, Mapping)
        and type(observed_at_by_device.get(device_id)) is int
    }
    top_observed_at = max((item["observed_at"] for item in evidence.values()), default=None)
    updated_at = top_observed_at if top_observed_at is not None else now
    return {"contract": {"name": "hausman-hub-climate-room-recovery-receipt-v2", "version": 2},
            "correlation_id": request["correlation_id"], "operation_id": operation_id, "request_id": request["request_id"],
            "request_fingerprint": request["request_fingerprint"], "request_snapshot": {"path_room_id": request["room_id"], "request": request["request"], "correlation_policy": request["correlation_policy"], "confirmation_window_ms": 30000, "reliability_profile": "climate_reliability_v1"},
            "room_id": request["room_id"], "expected_control_revision": preflight["control_revision"], "resulting_control_revision": preflight["control_revision"] + 1, "desired_snapshot_revision": preflight["control_revision"], "preflight_snapshot_fingerprint": preflight["preflight_snapshot_fingerprint"], "desired_snapshot_fingerprint": desired_fingerprint, "snapshot_token": preflight["snapshot_token"], "resolved_device_ids": list(selected), "desired_snapshot": desired,
            "status": status, "accepted": accepted, "confirmed": confirmed, "final": final, "duplicate": False, "dispatch_state": dispatch_state, "retry_policy": "forbidden_after_dispatch" if dispatch_state != "pre_dispatch_failed" else "allowed_pre_dispatch_only", "confirmation_window_ms": 30000,
            "read_back": {"attempted": bool(evidence), "matched": True if confirmed else (False if evidence else None), "observed_at": top_observed_at, "evidence": {"devices": evidence} if evidence else {}}, "message_code": status, "message": {"confirmed": "Восстановление подтверждено чтением состояния.", "partial": "Восстановление завершено частично.", "failed": "Восстановление не подтверждено.", "pending": "Восстановление ожидает подтверждения.", "unknown": "Результат восстановления пока неизвестен."}[status], "outcomes": outcomes, "created_at": now, "updated_at": updated_at}


def _recovery_receipt_observed_at(receipt: object) -> dict[str, int]:
    """Keep only persisted authoritative device evidence while rebuilding."""
    read_back = receipt.get("read_back") if isinstance(receipt, Mapping) else None
    evidence = read_back.get("evidence") if isinstance(read_back, Mapping) else None
    devices = evidence.get("devices") if isinstance(evidence, Mapping) else None
    if not isinstance(devices, Mapping):
        return {}
    return {
        device_id: item["observed_at"]
        for device_id, item in devices.items()
        if isinstance(device_id, str) and isinstance(item, Mapping)
        and type(item.get("observed_at")) is int
    }


def _recovery_device_matches(
    snapshot: Mapping[str, object],
    request: Mapping[str, object],
    device_id: str,
    desired: Mapping[str, object],
    dispatched_at: int | None = None,
    *,
    observed_at: int | None = None,
) -> bool:
    """Require current, device-specific evidence before confirming one leaf."""
    rooms = snapshot.get("rooms")
    room = next((item for item in rooms if isinstance(item, Mapping) and item.get("id") == request["room_id"]), None) if isinstance(rooms, list) else None
    devices = room.get("devices") if isinstance(room, Mapping) else None
    device = next((item for item in devices if isinstance(item, Mapping) and item.get("id") == device_id), None) if isinstance(devices, list) else None
    # Native projections expose a device observation timestamp.  Older
    # compatibility projections do not, so they cannot manufacture one from
    # a room aggregate and keep the established read-back behaviour.
    if observed_at is None:
        observed_at = device.get("observed_at") if isinstance(device, Mapping) else None
    source_observed_at = desired.get("source_observed_at")
    fresh_after_dispatch = (
        dispatched_at is None
        or (type(observed_at) is int and dispatched_at < observed_at <= dispatched_at + 30_000)
    )
    return bool(
        snapshot.get("fresh") is True
        and fresh_after_dispatch
        and type(source_observed_at) is int
        and type(observed_at) is int
        and observed_at > source_observed_at
        and isinstance(room, Mapping)
        and isinstance(device, Mapping)
        and device.get("available") is True
        and device.get("mode") == desired.get("mode") == "automatic"
        and device.get("reported_target_temperature") == desired.get("target_temperature")
        and device.get("reported_target_humidity") == desired.get("target_humidity")
    )
