"""Canonical tablet climate projection and durable typed operation service."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
import secrets
import time
from typing import Protocol

from ..correlation import resolve_correlation_id, validate_correlation_id
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

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SUPPORTED_ACTIONS = frozenset(
    {
        "set_home_targets",
        "synchronize_home",
        "set_room_target",
        "set_room_humidity_target",
        "clear_room_override",
        "set_room_mode",
        "set_device_mode",
    }
)
_SUPPORTED_ROOM_ACTIONS = frozenset(
    {
        "set_room_target",
        "set_room_humidity_target",
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


class ClimateTabletOperationNotFound(LookupError):
    """The requested bounded operation does not exist."""


class ClimateTabletOperationStore(Protocol):
    async def async_load(self) -> object | None: ...

    async def async_save(self, payload: dict[str, object]) -> None: ...


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

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            {
                "expected_state_revision": self.expected_state_revision,
                "action": self.action,
                "room_id": self.room_id,
                "parameters": self.parameters,
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
    if not required_fields <= set(payload) <= required_fields | {"correlation_id"}:
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
    if type(revision) is not int or not 0 <= revision <= 9_007_199_254_740_991:
        raise ClimateTabletViolation("climate action revision is invalid")
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
        or type(revision) is not int
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
    execution = contour.get("execution") if isinstance(contour, Mapping) else None
    settings_apply = (
        execution.get("settings_apply") if isinstance(execution, Mapping) else None
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
    home_targets_allowed = bool(
        home_base_allowed
        and isinstance(settings_apply, Mapping)
        and settings_apply.get("available") is True
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
        self._operation_id_factory = operation_id_factory or (
            lambda: secrets.token_hex(16)
        )
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._local_now = local_now or (lambda: datetime.now().astimezone())
        self._records_by_request: dict[str, _StoredOperation] = {}
        self._request_by_operation: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Restore only exact bounded operation records; damaged data fails closed."""

        payload = await self._store.async_load()
        if payload is None:
            return
        if not isinstance(payload, Mapping) or set(payload) != {"version", "records"}:
            raise ClimateTabletUnavailable("stored climate operations are invalid")
        if payload.get("version") != 1 or not isinstance(payload.get("records"), list):
            raise ClimateTabletUnavailable("stored climate operation version is invalid")
        records: dict[str, _StoredOperation] = {}
        operations: dict[str, str] = {}
        for item in payload["records"]:
            if not isinstance(item, Mapping) or set(item) != {
                "request_id", "fingerprint", "request", "receipt"
            }:
                raise ClimateTabletUnavailable("stored climate operation is invalid")
            request_id = item.get("request_id")
            fingerprint = item.get("fingerprint")
            request_payload = item.get("request")
            receipt = item.get("receipt")
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
            if request.request_id != request_id or request.fingerprint != fingerprint:
                raise ClimateTabletUnavailable("stored climate operation request is invalid")
            normalized = _validate_receipt(dict(receipt))
            if not _receipt_matches_request(normalized, request):
                raise ClimateTabletUnavailable("stored climate operation receipt is invalid")
            operation_id = normalized["operation_id"]
            if request_id in records or operation_id in operations:
                raise ClimateTabletUnavailable("stored climate operation is duplicated")
            records[request_id] = _StoredOperation(fingerprint, request, normalized)
            operations[operation_id] = request_id
        if len(records) > MAX_CLIMATE_OPERATION_RECORDS:
            raise ClimateTabletUnavailable("stored climate operation history is too large")
        self._records_by_request = records
        self._request_by_operation = operations

    async def async_snapshot(self) -> dict[str, object]:
        """Read the canonical runtime projection without changing climate state."""

        async with self._lock:
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
                return climate_tablet_snapshot(
                    None,
                    climate_mode=mode,
                    active_operations=active,
                    confirmed_operations=confirmed,
                    generated_at=self._safe_now(),
                )
            try:
                home = await self._runtime.async_public_snapshot()
            except Exception as error:
                raise ClimateTabletUnavailable("climate runtime is unavailable") from error
            return climate_tablet_snapshot(
                home,
                climate_mode=mode,
                active_operations=active,
                confirmed_operations=confirmed,
            )

    async def async_execute(self, payload: object) -> dict[str, object]:
        """Reserve, execute at most once, persist and return one operation receipt."""

        request = parse_climate_tablet_action(payload)
        async with self._lock:
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
            if len(self._records_by_request) >= MAX_CLIMATE_OPERATION_RECORDS:
                self._prune_oldest_final()
            if len(self._records_by_request) >= MAX_CLIMATE_OPERATION_RECORDS:
                raise ClimateTabletUnavailable("climate operation history is full")
            snapshot = await self._snapshot_unlocked()
            if snapshot["state_revision"] != request.expected_state_revision:
                raise ClimateTabletViolation(
                    "climate state revision changed",
                    code="revision_conflict",
                )
            _require_action_allowed(snapshot, request)
            operation_id = self._new_operation_id()
            now = self._safe_now()
            receipt = _pending_receipt(request, operation_id, now)
            self._remember(request, receipt)
            await self._async_save()
            try:
                result = await self._async_dispatch(request)
                final_snapshot = await self._snapshot_unlocked()
                receipt = _receipt_from_contour_result(
                    request,
                    operation_id,
                    result,
                    created_at=now,
                    updated_at=self._safe_now(),
                    resulting_state_revision=final_snapshot["state_revision"],
                )
            except (ContourApplyViolation, TemporaryTemperatureViolation, HomeClimateTargetsViolation) as error:
                receipt = _terminal_receipt(
                    request,
                    operation_id,
                    status="rejected",
                    reason="action_unsupported",
                    message=str(error) or "Климатическое действие отклонено.",
                    created_at=now,
                    updated_at=self._safe_now(),
                )
            except Exception:
                receipt = _terminal_receipt(
                    request,
                    operation_id,
                    status="unavailable",
                    reason="internal_error",
                    message="Не удалось надёжно получить результат климатической команды.",
                    created_at=now,
                    updated_at=self._safe_now(),
                )
            self._remember(request, receipt)
            await self._async_save()
            return dict(receipt)

    async def async_operation(self, operation_id: str) -> dict[str, object]:
        """Return one persisted receipt without repeating its physical command."""

        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            raise ClimateTabletOperationNotFound(operation_id)
        async with self._lock:
            await self._expire_pending_unlocked()
            await self._refresh_pending_unlocked()
            request_id = self._request_by_operation.get(operation_id)
            if request_id is None:
                raise ClimateTabletOperationNotFound(operation_id)
            record = self._records_by_request[request_id]
            return dict(record.receipt)

    async def _snapshot_unlocked(self) -> dict[str, object]:
        mode = _climate_mode(self._runtime)
        if mode == "disabled":
            return climate_tablet_snapshot(
                None, climate_mode=mode, generated_at=self._safe_now()
            )
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
        return climate_tablet_snapshot(
            home,
            climate_mode=mode,
            active_operations=active,
            confirmed_operations=confirmed,
        )

    async def _async_dispatch(self, request: ClimateTabletActionRequest) -> object:
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
    ) -> None:
        normalized = _validate_receipt(receipt)
        self._records_by_request[request.request_id] = _StoredOperation(
            request.fingerprint, request, normalized
        )
        self._request_by_operation[normalized["operation_id"]] = request.request_id

    async def _async_save(self) -> None:
        records = [
            {
                "request_id": request_id,
                "fingerprint": record.fingerprint,
                "request": _request_payload(record.request),
                "receipt": record.receipt,
            }
            for request_id, record in self._records_by_request.items()
        ]
        await self._store.async_save({"version": 1, "records": records})

    async def _expire_pending_unlocked(self) -> None:
        now = self._safe_now()
        changed = False
        for request_id, record in tuple(self._records_by_request.items()):
            receipt = record.receipt
            if receipt.get("final") is not False or now < receipt["expires_at"]:
                continue
            timed_out = _terminal_receipt(
                record.request,
                receipt["operation_id"],
                status="timed_out",
                reason="confirmation_timeout",
                message="Устройство не подтвердило новое состояние за отведённое время.",
                created_at=receipt["created_at"],
                updated_at=now,
            )
            self._records_by_request[request_id] = _StoredOperation(
                record.fingerprint, record.request, timed_out
            )
            changed = True
        if changed:
            await self._async_save()

    async def _refresh_pending_unlocked(self) -> None:
        pending = [
            (request_id, record)
            for request_id, record in self._records_by_request.items()
            if record.receipt.get("status") == "pending"
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
        completed = [
            (record.receipt["updated_at"], request_id, record)
            for request_id, record in self._records_by_request.items()
            if record.receipt.get("final") is True
        ]
        if not completed:
            return
        _, request_id, record = min(completed)
        self._records_by_request.pop(request_id, None)
        self._request_by_operation.pop(record.receipt["operation_id"], None)

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise ClimateTabletUnavailable("climate operation clock is invalid")
        return value


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


def _pending_receipt(
    request: ClimateTabletActionRequest, operation_id: str, now: int
) -> dict[str, object]:
    return {
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


def _request_payload(request: ClimateTabletActionRequest) -> dict[str, object]:
    return {
        "contract": {"name": CLIMATE_ACTION_CONTRACT_NAME, "version": 1},
        "correlation_id": request.correlation_id,
        "request_id": request.request_id,
        "expected_state_revision": request.expected_state_revision,
        "action": request.action,
        "room_id": request.room_id,
        "parameters": dict(request.parameters),
    }


def _receipt_matches_request(
    receipt: Mapping[str, object],
    request: ClimateTabletActionRequest,
) -> bool:
    return (
        receipt.get("request_id") == request.request_id
        and receipt.get("action") == request.action
        and receipt.get("room_id") == request.room_id
        and receipt.get("expected_state_revision")
        == request.expected_state_revision
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
        return snapshot.get("fresh") is True
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
    return _validate_receipt(receipt)


def _receipt_from_contour_result(
    request: ClimateTabletActionRequest,
    operation_id: str,
    result: object,
    *,
    created_at: int,
    updated_at: int,
    resulting_state_revision: object,
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
    return {
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


def _terminal_receipt(
    request: ClimateTabletActionRequest,
    operation_id: str,
    *,
    status: str,
    reason: str,
    message: str,
    created_at: int,
    updated_at: int,
) -> dict[str, object]:
    return {
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
    if set(receipt) != required:
        raise ClimateTabletUnavailable("climate operation receipt fields are invalid")
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
    if type(expected_revision) is not int or not 0 <= expected_revision <= 9_007_199_254_740_991:
        raise ClimateTabletUnavailable("climate operation revision is invalid")
    if resulting_revision is not None and (
        type(resulting_revision) is not int
        or not 0 <= resulting_revision <= 9_007_199_254_740_991
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
        and type(resulting_revision) is int
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
    if status in {"partial", "timed_out"} and not (
        flags[0:3] == (True, False, True) and reason != "none"
    ):
        raise ClimateTabletUnavailable("incomplete climate operation is inconsistent")
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
