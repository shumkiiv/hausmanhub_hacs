"""Explicitly apply one HausmanHub climate contour to the existing climate engine.

The existing ``hausman-climate`` runtime remains the algorithm and execution
owner.  This module only builds a bounded set of its already supported typed
configuration commands.  No caller can provide a backend command or payload.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
import secrets
import time
from typing import Any

from ..domain.climate import ClimateRegistry
from ..domain.climate_bridge import ClimateBridgeMode
from ..domain.contours import (
    ClimateContourRoom,
    ClimateProfile,
    ContourDefinition,
    ContourMode,
    climate_target_temperature,
)
from .climate_commands import (
    ClimateCommandPlan,
    ClimateCommandViolation,
    plan_climate_command,
)
from .climate_import import ClimateImportSnapshot


CONTOUR_APPLY_REQUEST_CONTRACT_NAME = "hausman-hub-contour-apply-request"
CONTOUR_APPLY_PREVIEW_CONTRACT_NAME = "hausman-hub-contour-apply-preview"
CONTOUR_APPLY_CONTRACT_VERSION = 1
CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME = "hausman-hub-climate-control-receipt"
CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION = 1
MAX_CONTOUR_APPLY_RECORDS = 256
MAX_CONTOUR_APPLY_COMMANDS = 128 * 3
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_OPERATION_ID = re.compile(r"^[a-f0-9]{32}$")
_STABLE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ContourApplyViolation(ValueError):
    """The requested contour cannot be safely applied."""


class ContourApplyStatus(StrEnum):
    """Coarse public result of one confirmed settings application."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    PARTIAL = "partial"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


class ClimateControlAction(StrEnum):
    """User-visible actions sharing one climate-control receipt."""

    APPLY_SAVED_SETTINGS = "apply_saved_settings"
    APPLY_SCHEDULE_PROFILE = "apply_schedule_profile"
    SET_TEMPORARY_TEMPERATURE = "set_temporary_temperature"
    RETURN_TO_SCHEDULE = "return_to_schedule"


_ACTION_NAMES = {
    ClimateControlAction.APPLY_SAVED_SETTINGS: "Применить настройки климата",
    ClimateControlAction.APPLY_SCHEDULE_PROFILE: "Переключить профиль по расписанию",
    ClimateControlAction.SET_TEMPORARY_TEMPERATURE: "Временно изменить температуру",
    ClimateControlAction.RETURN_TO_SCHEDULE: "Вернуть температуру по расписанию",
}
_STATUS_NAMES = {
    ContourApplyStatus.PENDING: "Проверяется",
    ContourApplyStatus.CONFIRMED: "Выполнено",
    ContourApplyStatus.PARTIAL: "Выполнено частично",
    ContourApplyStatus.REJECTED: "Отклонено",
    ContourApplyStatus.UNAVAILABLE: "Результат неизвестен",
}
_REASON_NAMES = {
    "already_in_sync": "Нужные настройки уже действуют.",
    "engine_rejected": "Климатическая система отклонила команду.",
    "command_result_unavailable": "Не удалось надёжно узнать результат команды.",
    "verification_unavailable": "Команда принята, но проверка результата пока недоступна.",
    "state_not_confirmed": "Новое состояние пока не подтверждено.",
}


@dataclass(frozen=True, slots=True)
class ClimateControlContext:
    """Exact public meaning of one contour-backed climate operation."""

    action: ClimateControlAction
    room_id: str | None = None
    target_temperature: float | None = None
    profile: ClimateProfile | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, ClimateControlAction):
            raise ContourApplyViolation("climate control action is invalid")
        room_action = self.action in {
            ClimateControlAction.SET_TEMPORARY_TEMPERATURE,
            ClimateControlAction.RETURN_TO_SCHEDULE,
        }
        if room_action:
            if (
                not isinstance(self.room_id, str)
                or _STABLE_ID.fullmatch(self.room_id) is None
            ):
                raise ContourApplyViolation("climate control room is invalid")
            try:
                normalized_temperature = climate_target_temperature(
                    self.target_temperature
                )
            except ValueError as error:
                raise ContourApplyViolation(str(error)) from error
            object.__setattr__(
                self,
                "target_temperature",
                normalized_temperature,
            )
            if self.profile is not None:
                raise ContourApplyViolation(
                    "room climate control cannot include a schedule profile"
                )
            return
        if self.room_id is not None or self.target_temperature is not None:
            raise ContourApplyViolation(
                "whole-contour climate control cannot include room values"
            )
        if self.action is ClimateControlAction.APPLY_SCHEDULE_PROFILE:
            if not isinstance(self.profile, ClimateProfile):
                raise ContourApplyViolation(
                    "scheduled climate control profile is invalid"
                )
        elif self.profile is not None:
            raise ContourApplyViolation(
                "manual contour application cannot include a schedule profile"
            )

    def as_payload(self) -> dict[str, object]:
        """Return one strict self-describing action block."""

        return {
            "code": self.action.value,
            "name": _ACTION_NAMES[self.action],
            "room_id": self.room_id,
            "target_temperature": self.target_temperature,
            "profile": None if self.profile is None else self.profile.value,
        }


@dataclass(frozen=True, slots=True)
class ContourApplyRoomExpectation:
    """Values that must be visible after climate-core accepts the commands."""

    room_id: str
    target_temperature: float
    target_strategy: str
    automatic: bool


@dataclass(frozen=True, slots=True)
class ContourApplyPlan:
    """One immutable, bounded application of a saved contour."""

    contour_id: str
    fingerprint: str
    rooms: tuple[ContourApplyRoomExpectation, ...]
    commands: tuple[ClimateCommandPlan, ...]
    temperature_changes: int
    strategy_changes: int
    automatic_mode_changes: int

    def preview_payload(self) -> dict[str, object]:
        """Return a public summary without private bindings or payloads."""

        return {
            "contract": {
                "name": CONTOUR_APPLY_PREVIEW_CONTRACT_NAME,
                "version": CONTOUR_APPLY_CONTRACT_VERSION,
            },
            "contour_id": self.contour_id,
            "status": "in_sync" if not self.commands else "ready",
            "ready": True,
            "room_count": len(self.rooms),
            "command_count": len(self.commands),
            "changes": {
                "temperature": self.temperature_changes,
                "strategy": self.strategy_changes,
                "automatic_mode": self.automatic_mode_changes,
            },
            "requires_confirmation": True,
            "parameters": {
                "temperature": True,
                "strategy": True,
                "automatic_mode": True,
                "humidity": False,
            },
            "limitations": ["room_humidity_command_not_supported"],
        }


@dataclass(frozen=True, slots=True)
class ContourApplyReceipt:
    """Idempotent public receipt for a multi-room contour application."""

    operation_id: str
    request_id: str
    contour_id: str
    context: ClimateControlContext
    status: ContourApplyStatus
    room_count: int
    command_count: int
    accepted_count: int
    confirmed_room_count: int
    temperature_changes: int
    strategy_changes: int
    automatic_mode_changes: int
    reasons: tuple[str, ...]
    created_at: int
    updated_at: int

    def __post_init__(self) -> None:
        if not isinstance(self.context, ClimateControlContext):
            raise ContourApplyViolation("climate control receipt context is invalid")
        if not isinstance(self.status, ContourApplyStatus):
            raise ContourApplyViolation("climate control receipt status is invalid")
        if any(reason not in _REASON_NAMES for reason in self.reasons):
            raise ContourApplyViolation("climate control receipt reason is invalid")

    def as_payload(self) -> dict[str, object]:
        """Return the exact public receipt shape."""

        return {
            "contract": {
                "name": CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME,
                "version": CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION,
            },
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            "contour_id": self.contour_id,
            "action": self.context.as_payload(),
            "status": self.status.value,
            "status_name": _STATUS_NAMES[self.status],
            "room_count": self.room_count,
            "command_count": self.command_count,
            "accepted_count": self.accepted_count,
            "confirmed_room_count": self.confirmed_room_count,
            "changes": {
                "temperature": self.temperature_changes,
                "strategy": self.strategy_changes,
                "automatic_mode": self.automatic_mode_changes,
            },
            "reasons": list(self.reasons),
            "reason_names": [_REASON_NAMES[reason] for reason in self.reasons],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class _ContourApplyRecord:
    plan: ContourApplyPlan
    receipt: ContourApplyReceipt


class _ContourApplyLedger:
    """Keep bounded idempotency records for the lifetime of one HausmanHub entry."""

    def __init__(
        self,
        *,
        operation_id_factory: Callable[[], str] | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._operation_id_factory = operation_id_factory or (
            lambda: secrets.token_hex(16)
        )
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._records: dict[str, _ContourApplyRecord] = {}

    def existing(
        self,
        request_id: str,
        fingerprint: str,
        context: ClimateControlContext,
    ) -> _ContourApplyRecord | None:
        """Return an identical prior request or reject conflicting reuse."""

        record = self._records.get(request_id)
        if record is None:
            return None
        if (
            record.plan.fingerprint != fingerprint
            or record.receipt.context != context
        ):
            raise ContourApplyViolation(
                "request id was already used for another climate operation"
            )
        return record

    def begin(
        self,
        request_id: str,
        plan: ContourApplyPlan,
        context: ClimateControlContext,
    ) -> _ContourApplyRecord:
        """Reserve idempotency before the first backend POST."""

        if len(self._records) >= MAX_CONTOUR_APPLY_RECORDS:
            raise ContourApplyViolation("contour apply history is full")
        operation_id = self._operation_id_factory()
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise RuntimeError("operation id factory returned an unsafe id")
        if any(
            record.receipt.operation_id == operation_id
            for record in self._records.values()
        ):
            raise RuntimeError("operation id factory returned a duplicate id")
        now = self._safe_now()
        receipt = ContourApplyReceipt(
            operation_id=operation_id,
            request_id=request_id,
            contour_id=plan.contour_id,
            context=context,
            status=(
                ContourApplyStatus.CONFIRMED
                if not plan.commands
                else ContourApplyStatus.PENDING
            ),
            room_count=len(plan.rooms),
            command_count=len(plan.commands),
            accepted_count=0,
            confirmed_room_count=(len(plan.rooms) if not plan.commands else 0),
            temperature_changes=plan.temperature_changes,
            strategy_changes=plan.strategy_changes,
            automatic_mode_changes=plan.automatic_mode_changes,
            reasons=(() if plan.commands else ("already_in_sync",)),
            created_at=now,
            updated_at=now,
        )
        record = _ContourApplyRecord(plan=plan, receipt=receipt)
        self._records[request_id] = record
        return record

    def update(
        self,
        request_id: str,
        *,
        status: ContourApplyStatus,
        accepted_count: int,
        confirmed_room_count: int,
        reasons: tuple[str, ...],
    ) -> _ContourApplyRecord:
        """Replace only bounded public progress fields."""

        record = self._records.get(request_id)
        if record is None:
            raise RuntimeError("contour apply record is unavailable")
        if not 0 <= accepted_count <= len(record.plan.commands):
            raise RuntimeError("accepted contour command count is invalid")
        if not 0 <= confirmed_room_count <= len(record.plan.rooms):
            raise RuntimeError("confirmed contour room count is invalid")
        receipt = replace(
            record.receipt,
            status=status,
            accepted_count=accepted_count,
            confirmed_room_count=confirmed_room_count,
            reasons=tuple(dict.fromkeys(reasons)),
            updated_at=self._safe_now(),
        )
        updated = replace(record, receipt=receipt)
        self._records[request_id] = updated
        return updated

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise RuntimeError("contour apply clock returned an unsafe timestamp")
        return value


def parse_contour_apply_request(payload: object) -> tuple[str, str]:
    """Require one explicit, idempotent confirmation from UI or Android."""

    if not isinstance(payload, Mapping) or any(
        not isinstance(key, str) for key in payload
    ):
        raise ContourApplyViolation("contour apply request must be an object")
    if set(payload) != {"request_id", "contour_id", "confirm"}:
        raise ContourApplyViolation("contour apply request fields are invalid")
    request_id = payload.get("request_id")
    contour_id = payload.get("contour_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ContourApplyViolation("request id must be one stable public id")
    if contour_id != "climate":
        raise ContourApplyViolation("only the climate contour can be applied")
    if payload.get("confirm") is not True:
        raise ContourApplyViolation("contour apply requires explicit confirmation")
    return request_id, contour_id


def build_contour_apply_plan(
    contour: ContourDefinition,
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
    *,
    room_ids: tuple[str, ...] | None = None,
) -> ContourApplyPlan:
    """Build supported changes from saved public settings and fresh state."""

    if not isinstance(contour, ContourDefinition) or contour.contour_id != "climate":
        raise ContourApplyViolation("climate contour is not configured")
    if contour.mode is not ContourMode.AUTOMATIC:
        raise ContourApplyViolation("climate contour is not in automatic mode")
    if not isinstance(registry, ClimateRegistry):
        raise ContourApplyViolation("climate registry is unavailable")
    if not isinstance(snapshot, ClimateImportSnapshot) or not snapshot.runtime_fresh:
        raise ContourApplyViolation("climate engine state is stale")

    assignments = _selected_assignments(contour, room_ids)
    commands: list[ClimateCommandPlan] = []
    rooms: list[ContourApplyRoomExpectation] = []
    temperature_changes = 0
    strategy_changes = 0
    automatic_mode_changes = 0
    for assignment in assignments:
        imported_room = snapshot.room(assignment.room_id)
        if imported_room is None:
            raise ContourApplyViolation("contour room state is unavailable")
        for device_id in assignment.device_ids:
            device = registry.device(device_id)
            imported_device = (
                None if device is None else snapshot.device(device.source_id)
            )
            if (
                device is None
                or device.room_id != assignment.room_id
                or imported_device is None
                or imported_device.room_id != assignment.room_id
                or not imported_device.available
            ):
                raise ContourApplyViolation("contour device is unavailable")

        rooms.append(
            ContourApplyRoomExpectation(
                room_id=assignment.room_id,
                target_temperature=assignment.target_temperature,
                target_strategy=assignment.strategy.value,
                automatic=True,
            )
        )
        # Apply the strategy first, then the comfort target, and switch the
        # existing engine to automatic only after those values are accepted.
        if imported_room.target_strategy != assignment.strategy.value:
            commands.append(
                _configuration_command(
                    {
                        "action": "set_room_target_strategy",
                        "room_id": assignment.room_id,
                        "target_strategy": assignment.strategy.value,
                    },
                    registry,
                    snapshot,
                )
            )
            strategy_changes += 1
        if not _same_number(
            imported_room.target_temperature,
            assignment.target_temperature,
        ):
            commands.append(
                _configuration_command(
                    {
                        "action": "set_room_target",
                        "room_id": assignment.room_id,
                        "target_temperature": assignment.target_temperature,
                    },
                    registry,
                    snapshot,
                )
            )
            temperature_changes += 1
        if imported_room.mode not in {"auto", "forced_auto_only"}:
            commands.append(
                _configuration_command(
                    {
                        "action": "set_room_mode",
                        "room_id": assignment.room_id,
                        "mode": "auto",
                    },
                    registry,
                    snapshot,
                )
            )
            automatic_mode_changes += 1

    if len(commands) > MAX_CONTOUR_APPLY_COMMANDS:
        raise ContourApplyViolation("contour apply has too many commands")
    return ContourApplyPlan(
        contour_id=contour.contour_id,
        fingerprint=_contour_fingerprint(contour, room_ids=room_ids),
        rooms=tuple(rooms),
        commands=tuple(commands),
        temperature_changes=temperature_changes,
        strategy_changes=strategy_changes,
        automatic_mode_changes=automatic_mode_changes,
    )


def confirmed_contour_room_count(
    plan: ContourApplyPlan,
    snapshot: ClimateImportSnapshot,
) -> int:
    """Count only rooms whose three supported settings are observable."""

    count = 0
    for expected in plan.rooms:
        room = snapshot.room(expected.room_id)
        if (
            room is not None
            and _same_number(room.target_temperature, expected.target_temperature)
            and room.target_strategy == expected.target_strategy
            and room.mode in {"auto", "forced_auto_only"}
        ):
            count += 1
    return count


def contour_fingerprint(
    contour: ContourDefinition,
    *,
    room_ids: tuple[str, ...] | None = None,
) -> str:
    """Expose the deterministic desired-state fingerprint only internally."""

    if not isinstance(contour, ContourDefinition):
        raise ContourApplyViolation("contour definition is unavailable")
    return _contour_fingerprint(contour, room_ids=room_ids)


def _configuration_command(
    request: Mapping[str, Any],
    registry: ClimateRegistry,
    snapshot: ClimateImportSnapshot,
) -> ClimateCommandPlan:
    """Reuse the typed translator while bypassing the legacy one-room canary."""

    try:
        plan = plan_climate_command(
            request,
            registry,
            snapshot,
            bridge_mode=ClimateBridgeMode.SHADOW,
        )
    except ClimateCommandViolation as error:
        raise ContourApplyViolation(str(error)) from error
    return replace(plan, execute=True)


def _selected_assignments(
    contour: ContourDefinition,
    room_ids: tuple[str, ...] | None,
) -> tuple[ClimateContourRoom, ...]:
    if room_ids is None:
        return contour.rooms
    if (
        not isinstance(room_ids, tuple)
        or not room_ids
        or any(not isinstance(room_id, str) for room_id in room_ids)
        or len(room_ids) != len(set(room_ids))
    ):
        raise ContourApplyViolation("contour apply room scope is invalid")
    requested = set(room_ids)
    assignments = tuple(
        room for room in contour.rooms if room.room_id in requested
    )
    if {room.room_id for room in assignments} != requested:
        raise ContourApplyViolation("contour apply room is not configured")
    return assignments


def _contour_fingerprint(
    contour: ContourDefinition,
    *,
    room_ids: tuple[str, ...] | None = None,
) -> str:
    assignments = _selected_assignments(contour, room_ids)
    canonical = json.dumps(
        {
            "id": contour.contour_id,
            "mode": contour.mode.value,
            "scope": [room.room_id for room in assignments],
            "rooms": [
                {
                    "id": room.room_id,
                    "devices": list(room.device_ids),
                    "temperature": room.target_temperature,
                    "humidity": room.target_humidity,
                    "strategy": room.strategy.value,
                }
                for room in assignments
            ],
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def _same_number(left: object, right: object) -> bool:
    return (
        not isinstance(left, bool)
        and isinstance(left, (int, float))
        and not isinstance(right, bool)
        and isinstance(right, (int, float))
        and abs(float(left) - float(right)) < 0.01
    )
