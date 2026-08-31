from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import re
import secrets
import time

from ..correlation import CorrelationIdError, validate_correlation_id
from ..domain.climate import ClimateDeviceKind, ClimateRegistry
from ..domain.climate_bridge import ClimateControlMode
from ..domain.climate_observation import ClimateObservationSnapshot
from ..domain.contours import (
    ClimateContourRoom,
    ClimateProfile,
    ContourDefinition,
    ContourMode,
    climate_target_temperature,
)
from .climate_application import (
    ClimateApplicationPlan,
    ClimateApplicationViolation,
    ClimateDesiredStateChanges,
    build_climate_application_plan,
)


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


@dataclass(frozen=True, slots=True)
class ContourApplyRequest:
    """Validated public apply request.

    Keeping negotiated reliability fields on the typed boundary prevents the
    runtime from accidentally treating a legacy request as an enhanced one.
    """

    request_id: str
    contour_id: str
    room_ids: tuple[str, ...] | None
    correlation_id: str | None
    reliability_profile: str | None = None
    expected_control_revision: int | None = None
    schedule_profile: ClimateProfile | None = None


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
        payload = {
            "code": self.action.value,
            "name": _ACTION_NAMES[self.action],
            "room_id": self.room_id,
            "target_temperature": (
                None if self.action is ClimateControlAction.RETURN_TO_SCHEDULE
                else self.target_temperature
            ),
            "profile": None if self.profile is None else self.profile.value,
        }
        if self.action is ClimateControlAction.RETURN_TO_SCHEDULE:
            payload["resulting_target_temperature"] = self.target_temperature
        return payload


@dataclass(frozen=True, slots=True)
class ContourApplyPlan:
    native_plan: ClimateApplicationPlan

    def __post_init__(self) -> None:
        if not isinstance(self.native_plan, ClimateApplicationPlan):
            raise ContourApplyViolation("native climate application plan is invalid")

    @property
    def contour_id(self) -> str:
        return self.native_plan.contour_id

    @property
    def fingerprint(self) -> str:
        return self.native_plan.fingerprint

    @property
    def target_room_ids(self) -> tuple[str, ...]:
        return self.native_plan.target_room_ids

    @property
    def strict_calls(self):
        return self.native_plan.strict_calls

    @property
    def desired_state_changes(self) -> ClimateDesiredStateChanges:
        return self.native_plan.desired_state_changes

    def preview_payload(self) -> dict[str, object]:
        return {
            "contract": {
                "name": CONTOUR_APPLY_PREVIEW_CONTRACT_NAME,
                "version": CONTOUR_APPLY_CONTRACT_VERSION,
            },
            "contour_id": self.contour_id,
            "status": "unavailable" if not self.native_plan.preflight_permitted else (
                "in_sync" if not self.strict_calls else "ready"
            ),
            "ready": self.native_plan.preflight_permitted,
            "room_count": len(self.target_room_ids),
            "command_count": len(self.strict_calls),
            "changes": {
                "temperature": self.desired_state_changes.temperature,
                "strategy": self.desired_state_changes.strategy,
                "automatic_mode": self.desired_state_changes.automatic_mode,
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
    correlation_id: str | None
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
    enhanced: Mapping[str, object] | None = None
    # Exact per-owner execution data is kept separate from aggregate counts.
    # It is used by the reliability adapter to avoid claiming that a failed
    # first leaf says anything about an independently attempted neighbour.
    device_outcomes: Mapping[str, Mapping[str, object]] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.context, ClimateControlContext):
            raise ContourApplyViolation("climate control receipt context is invalid")
        if not isinstance(self.status, ContourApplyStatus):
            raise ContourApplyViolation("climate control receipt status is invalid")
        if any(reason not in _REASON_NAMES for reason in self.reasons):
            raise ContourApplyViolation("climate control receipt reason is invalid")

    def as_payload(self) -> dict[str, object]:
        """Return the exact public receipt shape."""

        accepted = self.status in {
            ContourApplyStatus.PENDING,
            ContourApplyStatus.CONFIRMED,
            ContourApplyStatus.PARTIAL,
        }
        confirmed = self.status is ContourApplyStatus.CONFIRMED
        message = {
            ContourApplyStatus.PENDING: (
                "Команда принята; климатический контур ещё проверяет новые цели."
            ),
            ContourApplyStatus.CONFIRMED: (
                "Климатический контур подтвердил новые цели."
            ),
            ContourApplyStatus.PARTIAL: (
                "Часть комнат подтвердила новые цели; проверьте остальные."
            ),
            ContourApplyStatus.REJECTED: "Климатический контур отклонил команду.",
            ContourApplyStatus.UNAVAILABLE: (
                "Не удалось надёжно получить результат команды."
            ),
        }[self.status]
        payload = {
            "contract": {
                "name": CLIMATE_CONTROL_RECEIPT_CONTRACT_NAME,
                "version": CLIMATE_CONTROL_RECEIPT_CONTRACT_VERSION,
            },
            "operation_id": self.operation_id,
            "request_id": self.request_id,
            **(
                {"correlation_id": self.correlation_id}
                if self.correlation_id is not None
                else {}
            ),
            "contour_id": self.contour_id,
            "action": self.context.as_payload(),
            "status": self.status.value,
            "status_name": _STATUS_NAMES[self.status],
            "accepted": accepted,
            "confirmed": confirmed,
            "message": message,
            "confirmation_window_ms": 8000,
            "read_back": {
                "attempted": self.command_count == 0 or self.accepted_count > 0,
                "matched": confirmed,
                "observed_at": self.updated_at,
                "confirmed_room_count": self.confirmed_room_count,
            },
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
        if self.enhanced is not None:
            payload.update(_enhanced_payload(payload, self.enhanced))
        return payload


@dataclass(frozen=True, slots=True)
class _ContourApplyRecord:
    plan: ContourApplyPlan | "_RestoredContourApplyPlan"
    receipt: ContourApplyReceipt
    # Public enhanced data is retained together with the frozen plan. It is
    # never reconstructed from a later contour, which makes replay truthful.
    enhanced: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class _RestoredContourApplyPlan:
    """A receipt-only plan: eligible for replay, never for dispatch."""

    fingerprint: str


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
        correlation_id: str | None = None,
    ) -> _ContourApplyRecord | None:
        """Return an identical prior request or reject conflicting reuse."""

        record = self._records.get(request_id)
        if record is None:
            return None
        if (
            record.plan.fingerprint != fingerprint
            or record.receipt.context != context
            or (
                correlation_id is not None
                and record.receipt.correlation_id != correlation_id
            )
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
        correlation_id: str | None = None,
        enhanced: Mapping[str, object] | None = None,
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
            correlation_id=correlation_id,
            contour_id=plan.contour_id,
            context=context,
            status=(
                ContourApplyStatus.UNAVAILABLE
                if not plan.native_plan.preflight_permitted
                else (
                    ContourApplyStatus.CONFIRMED
                    if not plan.strict_calls
                    else ContourApplyStatus.PENDING
                )
            ),
            room_count=len(plan.target_room_ids),
            command_count=len(plan.strict_calls),
            accepted_count=0,
            confirmed_room_count=len(plan.native_plan.initially_aligned_room_ids),
            temperature_changes=plan.desired_state_changes.temperature,
            strategy_changes=plan.desired_state_changes.strategy,
            automatic_mode_changes=plan.desired_state_changes.automatic_mode,
            reasons=(
                ("engine_rejected",)
                if not plan.native_plan.preflight_permitted
                else (() if plan.strict_calls else ("already_in_sync",))
            ),
            created_at=now,
            updated_at=now,
            enhanced=enhanced,
        )
        record = _ContourApplyRecord(plan=plan, receipt=receipt, enhanced=enhanced)
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
        if not 0 <= accepted_count <= len(record.plan.strict_calls):
            raise RuntimeError("accepted contour command count is invalid")
        if not 0 <= confirmed_room_count <= len(record.plan.target_room_ids):
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

    def by_operation(self, operation_id: str) -> _ContourApplyRecord | None:
        """Return a frozen receipt for polling, without dispatching again."""

        if not isinstance(operation_id, str) or _OPERATION_ID.fullmatch(operation_id) is None:
            return None
        return next(
            (record for record in self._records.values()
             if record.receipt.operation_id == operation_id),
            None,
        )

    def by_request(self, request_id: str) -> _ContourApplyRecord | None:
        return self._records.get(request_id)

    def discard_unpersisted(self, request_id: str) -> None:
        """Forget a record whose first durable save failed before dispatch."""

        self._records.pop(request_id, None)

    def serialized(self) -> list[dict[str, object]]:
        """Return bounded public-safe records for restart recovery."""

        return [
            {"request_id": request_id, "fingerprint": record.plan.fingerprint,
             "context": {"action": record.receipt.context.action.value,
                         "room_id": record.receipt.context.room_id,
                         "target_temperature": record.receipt.context.target_temperature,
                         "profile": None if record.receipt.context.profile is None else record.receipt.context.profile.value},
             "receipt": record.receipt.as_payload()}
            for request_id, record in self._records.items()
        ]

    def restore(self, records: object) -> None:
        """Restore terminal or pending receipts without making them dispatchable."""

        if records is None:
            return
        if not isinstance(records, list) or len(records) > MAX_CONTOUR_APPLY_RECORDS:
            raise ContourApplyViolation("stored direct control ledger is invalid")
        restored: dict[str, _ContourApplyRecord] = {}
        for item in records:
            if not isinstance(item, Mapping) or set(item) != {"request_id", "fingerprint", "context", "receipt"}:
                raise ContourApplyViolation("stored direct control record is invalid")
            request_id, fingerprint, context_raw, payload = item["request_id"], item["fingerprint"], item["context"], item["receipt"]
            if (not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None
                    or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
                    or not isinstance(context_raw, Mapping) or not isinstance(payload, Mapping)):
                raise ContourApplyViolation("stored direct control record is invalid")
            try:
                action = ClimateControlAction(context_raw.get("action"))
                profile = context_raw.get("profile")
                context = ClimateControlContext(action=action, room_id=context_raw.get("room_id"),
                    target_temperature=context_raw.get("target_temperature"),
                    profile=None if profile is None else ClimateProfile(profile))
                receipt = _receipt_from_stored_payload(dict(payload), context)
            except (TypeError, ValueError, KeyError) as error:
                raise ContourApplyViolation("stored direct control record is invalid") from error
            if receipt.request_id != request_id or receipt.operation_id in {
                item.receipt.operation_id for item in restored.values()
            }:
                raise ContourApplyViolation("stored direct control record is duplicated")
            plan = _RestoredContourApplyPlan(fingerprint)
            restored[request_id] = _ContourApplyRecord(plan=plan, receipt=receipt, enhanced=receipt.enhanced)
        self._records = restored

    @property
    def control_revision(self) -> int:
        """Highest accepted negotiated revision restored from durable receipts."""

        values = [
            record.receipt.enhanced.get("resulting_control_revision", 0)
            for record in self._records.values()
            if record.receipt.enhanced is not None
        ]
        return max((value for value in values if type(value) is int), default=0)

    def _safe_now(self) -> int:
        value = self._now_ms()
        if type(value) is not int or value < 0:
            raise RuntimeError("contour apply clock returned an unsafe timestamp")
        return value


def parse_contour_apply_request(
    payload: object,
) -> ContourApplyRequest:
    """Require one explicit, idempotent confirmation from UI or Android.

    An optional ``room_ids`` scope limits the application to the listed
    configured rooms; without it the whole contour is applied.
    """

    if not isinstance(payload, Mapping) or any(
        not isinstance(key, str) for key in payload
    ):
        raise ContourApplyViolation("contour apply request must be an object")
    if not {"request_id", "contour_id", "confirm"} <= set(payload) <= {
        "request_id",
        "contour_id",
        "confirm",
        "room_ids",
        "correlation_id", "reliability_profile", "expected_control_revision",
        "schedule_profile",
    }:
        raise ContourApplyViolation("contour apply request fields are invalid")
    request_id = payload.get("request_id")
    contour_id = payload.get("contour_id")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ContourApplyViolation("request id must be one stable public id")
    if contour_id != "climate":
        raise ContourApplyViolation("only the climate contour can be applied")
    if payload.get("confirm") is not True:
        raise ContourApplyViolation("contour apply requires explicit confirmation")
    correlation_id = None
    if "correlation_id" in payload:
        try:
            correlation_id = validate_correlation_id(payload["correlation_id"])
        except CorrelationIdError as error:
            raise ContourApplyViolation("correlation id is invalid") from error
    reliability_profile = payload.get("reliability_profile")
    expected_control_revision = payload.get("expected_control_revision")
    if reliability_profile is not None:
        if reliability_profile != "climate_reliability_v1" or (
            type(expected_control_revision) is not int
            or expected_control_revision < 0
            or expected_control_revision > 9007199254740991
        ):
            raise ContourApplyViolation("contour apply reliability request is invalid")
    elif expected_control_revision is not None:
        raise ContourApplyViolation("control revision requires reliability profile")
    schedule_profile = payload.get("schedule_profile")
    if schedule_profile is not None:
        if reliability_profile is None:
            raise ContourApplyViolation("schedule profile requires reliability profile")
        try:
            schedule_profile = ClimateProfile(schedule_profile)
        except (TypeError, ValueError) as error:
            raise ContourApplyViolation("schedule profile is invalid") from error
    room_ids = payload.get("room_ids")
    if room_ids is None:
        return ContourApplyRequest(
            request_id, contour_id, None, correlation_id, reliability_profile,
            expected_control_revision, schedule_profile,
        )
    if (
        not isinstance(room_ids, list)
        or not room_ids
        or len(room_ids) > 64
        or any(
            not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None
            for room_id in room_ids
        )
        or len(room_ids) != len(set(room_ids))
    ):
        raise ContourApplyViolation("contour apply room scope is invalid")
    return ContourApplyRequest(
        request_id, contour_id, tuple(room_ids), correlation_id,
        reliability_profile, expected_control_revision, schedule_profile,
    )


def build_contour_apply_plan(
    contour: ContourDefinition,
    registry: ClimateRegistry,
    bridge_mode: ClimateControlMode,
    observation: ClimateObservationSnapshot,
    *,
    room_ids: tuple[str, ...] | None = None,
    desired_state_changes: ClimateDesiredStateChanges,
) -> ContourApplyPlan:
    assignments = _selected_assignments(contour, room_ids)
    application_contour = _temperature_only_application_contour(
        contour,
        registry,
        target_room_ids=tuple(assignment.room_id for assignment in assignments),
        desired_state_changes=desired_state_changes,
    )
    try:
        native_plan = build_climate_application_plan(
            application_contour,
            registry,
            bridge_mode,
            observation,
            fingerprint=_contour_fingerprint(contour, room_ids=room_ids),
            target_room_ids=tuple(assignment.room_id for assignment in assignments),
            desired_state_changes=desired_state_changes,
        )
    except ClimateApplicationViolation as error:
        raise ContourApplyViolation(str(error)) from error
    if len(native_plan.strict_calls) > MAX_CONTOUR_APPLY_COMMANDS:
        raise ContourApplyViolation("contour apply has too many strict calls")
    return ContourApplyPlan(native_plan=native_plan)


def _temperature_only_application_contour(
    contour: ContourDefinition,
    registry: ClimateRegistry,
    *,
    target_room_ids: tuple[str, ...],
    desired_state_changes: ClimateDesiredStateChanges,
) -> ContourDefinition:
    """Limit an explicit temperature operation to its actual actuator."""

    if (
        desired_state_changes.temperature <= 0
        or desired_state_changes.strategy != 0
        or desired_state_changes.automatic_mode != 0
    ):
        return contour
    targeted = frozenset(target_room_ids)
    return replace(
        contour,
        rooms=tuple(
            replace(
                room,
                device_ids=tuple(
                    device_id
                    for device_id in room.device_ids
                    if (
                        (device := registry.device(device_id)) is not None
                        and device.kind is ClimateDeviceKind.AIR_CONDITIONER
                    )
                ),
            )
            if room.room_id in targeted
            else room
            for room in contour.rooms
        ),
    )


def local_desired_state_changes(
    previous: ContourDefinition,
    current: ContourDefinition,
    *,
    target_room_ids: tuple[str, ...] | None = None,
) -> ClimateDesiredStateChanges:
    if (
        not isinstance(previous, ContourDefinition)
        or not isinstance(current, ContourDefinition)
        or previous.contour_id != "climate"
        or current.contour_id != "climate"
    ):
        raise ContourApplyViolation("climate contours are unavailable")
    assignments = _selected_assignments(current, target_room_ids)
    previous_rooms = {room.room_id: room for room in previous.rooms}
    temperature_changes = 0
    strategy_changes = 0
    for assignment in assignments:
        prior = previous_rooms.get(assignment.room_id)
        if prior is None:
            raise ContourApplyViolation("previous climate room is unavailable")
        if not _same_number(prior.target_temperature, assignment.target_temperature):
            temperature_changes += 1
        if prior.strategy is not assignment.strategy:
            strategy_changes += 1
    return ClimateDesiredStateChanges(
        temperature=temperature_changes,
        strategy=strategy_changes,
        automatic_mode=0,
    )


def contour_fingerprint(
    contour: ContourDefinition,
    *,
    room_ids: tuple[str, ...] | None = None,
) -> str:
    """Expose the deterministic desired-state fingerprint only internally."""

    if not isinstance(contour, ContourDefinition):
        raise ContourApplyViolation("contour definition is unavailable")
    return _contour_fingerprint(contour, room_ids=room_ids)


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


def _enhanced_payload(
    payload: Mapping[str, object], metadata: Mapping[str, object],
) -> dict[str, object]:
    """Project the negotiated receipt from frozen per-leaf metadata."""

    scope = metadata["resolved_scope"]
    desired = metadata["desired_snapshot"]
    fingerprint = metadata["request_fingerprint"]
    expected = metadata["expected_control_revision"]
    resulting = metadata["resulting_control_revision"]
    action_parameters = metadata["parameters"]
    action = payload["action"]
    status = payload["status"]
    confirmed = status == ContourApplyStatus.CONFIRMED.value
    pending = status in {ContourApplyStatus.PENDING.value, ContourApplyStatus.PARTIAL.value}
    ledger = metadata.get("leaf_ledger", {})
    leaves: dict[str, dict[str, object]] = {}
    rooms: dict[str, dict[str, object]] = {}
    for row in scope["devices_by_room"]:  # type: ignore[index]
        room_id = row["room_id"]  # type: ignore[index]
        room_devices: dict[str, object] = {}
        for device_id in row["device_ids"]:  # type: ignore[index]
            actual = desired[device_id]  # type: ignore[index]
            leaf_state = ledger.get(device_id) if isinstance(ledger, Mapping) else None
            if leaf_state == "pending_dispatch":
                leaf = {"status": "pending", "reason": "none", "execution_state": "pending_dispatch",
                        "message_code": "pending",
                        "message": "Команда принята и ожидает отправки.", "command_count": 0, "accepted_count": 0}
            elif leaf_state == "started":
                leaf = {"status": "failed", "reason": "command_failed", "execution_state": "dispatched_not_accepted",
                        "retry_policy": "forbidden_after_dispatch", "message_code": "command_failed",
                        "message": "Отправка была начата, результат требует проверки.", "command_count": 1, "accepted_count": 0}
            elif leaf_state == "dispatched_not_accepted":
                leaf = {"status": "failed", "reason": "command_failed", "execution_state": "dispatched_not_accepted",
                        "retry_policy": "forbidden_after_dispatch", "message_code": "command_failed",
                        "message": "Команда не была принята устройством.", "command_count": 1, "accepted_count": 0}
            elif leaf_state == "accepted_unverified":
                leaf = {"status": "pending", "reason": "none", "execution_state": "accepted_unverified",
                        "retry_policy": "forbidden_after_dispatch", "message_code": "pending",
                        "message": "Команда принята и ожидает чтения состояния.", "command_count": 1, "accepted_count": 1}
            elif confirmed and payload["command_count"] == 0:
                leaf = {
                    "status": "confirmed", "reason": "none",
                    "execution_state": "already_in_sync",
                    "message_code": "confirmed",
                    "message": "Устройство уже соответствует сохранённой цели.",
                    "command_count": 0, "accepted_count": 0,
                    "evidence": {
                        "desired_target_temperature": actual["target_temperature"],
                        "desired_target_humidity": actual["target_humidity"],
                        "reported_target_temperature": actual["target_temperature"],
                        "reported_target_humidity": actual["target_humidity"],
                        "observed_actual": actual, "observed_at": payload["updated_at"], "fresh": True,
                        "action": {"request_fingerprint": fingerprint, "action": action["code"], "parameters": action_parameters},
                    },
                }
            elif confirmed:
                leaf = {
                    "status": "confirmed", "reason": "none", "execution_state": "applied",
                    "message_code": "confirmed", "message": "Результат подтверждён чтением состояния.",
                    "command_count": 1, "accepted_count": 1,
                    "evidence": {
                        "desired_target_temperature": actual["target_temperature"],
                        "desired_target_humidity": actual["target_humidity"],
                        "reported_target_temperature": actual["target_temperature"],
                        "reported_target_humidity": actual["target_humidity"],
                        "observed_actual": actual, "observed_at": payload["updated_at"], "fresh": True,
                        "action": {"request_fingerprint": fingerprint, "action": action["code"], "parameters": action_parameters},
                    },
                }
            elif pending:
                leaf = {"status": "pending", "reason": "none", "execution_state": "accepted_unverified",
                        "retry_policy": "forbidden_after_dispatch", "message_code": "pending",
                        "message": "Команда принята и ожидает отправки.", "command_count": 1, "accepted_count": 1}
            else:
                leaf = {"status": "failed", "reason": "command_failed", "execution_state": "dispatched_not_accepted",
                        "retry_policy": "forbidden_after_dispatch", "message_code": "command_failed",
                        "message": "Команда не подтверждена, требуется проверка устройства.", "command_count": 1, "accepted_count": 0}
            leaves[device_id] = leaf
            room_devices[device_id] = leaf
        if confirmed:
            room_status = {"status": "confirmed", "reason": "none", "execution_state": "applied", "message_code": "confirmed", "message": "Результат подтверждён чтением состояния.", "devices": room_devices}
        elif pending:
            room_status = {"status": "pending", "reason": "none", "execution_state": "accepted_unverified", "message_code": "pending", "message": "Команда принята и ожидает отправки.", "devices": room_devices}
        else:
            room_status = {"status": "partial", "reason": "none", "message_code": "partial", "message": "Результаты устройств различаются.", "devices": room_devices}
        rooms[room_id] = room_status
    message_code = {"confirmed": "confirmed", "partial": "partial", "pending": "pending", "rejected": "rejected", "unavailable": "unavailable"}[status]  # type: ignore[index]
    message = {"confirmed": "Результат подтверждён чтением состояния.", "partial": "Цель сохранена, часть устройств ожидает применения.", "pending": "Команда принята и ожидает подтверждения.", "rejected": "Команда отклонена.", "unavailable": "Результат команды пока недоступен."}[status]  # type: ignore[index]
    return {
        "duplicate": False, "action_snapshot": {"kind": metadata["kind"], "request_fingerprint": fingerprint,
            "action": action["code"], "parameters": action_parameters, "resolved_scope": scope, "control_revision": expected},
        "desired_snapshot": desired, "desired_snapshot_fingerprint": metadata["desired_snapshot_fingerprint"],
        "expected_control_revision": expected, "resulting_control_revision": resulting,
        "message_code": message_code, "message": message, "request_fingerprint": fingerprint,
        "confirmation_window_ms": 8000, "final": not pending, "unfinished_device_count": 0 if confirmed else len(leaves),
        "read_back": {"attempted": payload["accepted"], "matched": confirmed, "observed_at": payload["updated_at"] if payload["accepted"] else None,
           "room_count": payload["room_count"], "confirmed_room_count": payload["confirmed_room_count"],
           "evidence": _enhanced_evidence(action, payload)},
        "intent": {"status": "saved_and_applied" if confirmed else ("saved_pending_confirmation" if pending else "unsaved_unavailable"),
            "request_fingerprint": fingerprint, "control_revision": resulting, "scope_revision": resulting,
            "scope_fingerprint": metadata["scope_fingerprint"], "resolved_scope": scope,
            "desired_target_temperature": metadata["desired_target_temperature"], "desired_target_humidity": metadata["desired_target_humidity"]},
        "outcomes": {"rooms": rooms},
    }


def _enhanced_evidence(action: Mapping[str, object], payload: Mapping[str, object]) -> dict[str, object]:
    value: dict[str, object] = {"action_code": action["code"], "room_count": payload["room_count"], "confirmed_room_count": payload["confirmed_room_count"], "accepted_count": payload["accepted_count"]}
    if action["room_id"] is not None:
        value["room_id"] = action["room_id"]
        value["target_temperature"] = action["target_temperature"]
    if action["code"] == ClimateControlAction.RETURN_TO_SCHEDULE.value:
        value["resulting_target_temperature"] = action["resulting_target_temperature"]
    return value


def _receipt_from_stored_payload(
    payload: dict[str, object], context: ClimateControlContext,
) -> ContourApplyReceipt:
    """Validate the immutable public fields needed for restart-safe polling."""

    status = ContourApplyStatus(payload["status"])
    enhanced = None
    if "action_snapshot" in payload:
        action_snapshot = payload["action_snapshot"]
        intent = payload["intent"]
        if not isinstance(action_snapshot, Mapping) or not isinstance(intent, Mapping):
            raise ValueError("enhanced receipt is invalid")
        enhanced = {
            "kind": action_snapshot["kind"], "request_fingerprint": action_snapshot["request_fingerprint"],
            "parameters": dict(action_snapshot["parameters"]), "resolved_scope": dict(action_snapshot["resolved_scope"]),
            "desired_snapshot": dict(payload["desired_snapshot"]),
            "desired_snapshot_fingerprint": payload["desired_snapshot_fingerprint"],
            "scope_fingerprint": intent["scope_fingerprint"],
            "expected_control_revision": payload["expected_control_revision"],
            "resulting_control_revision": payload["resulting_control_revision"],
            "desired_target_temperature": intent["desired_target_temperature"],
            "desired_target_humidity": intent["desired_target_humidity"],
        }
        outcomes = payload.get("outcomes")
        rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
        leaf_ledger: dict[str, str] = {}
        if isinstance(rooms, Mapping):
            for room in rooms.values():
                devices = room.get("devices") if isinstance(room, Mapping) else None
                if not isinstance(devices, Mapping):
                    continue
                for device_id, leaf in devices.items():
                    state = leaf.get("execution_state") if isinstance(leaf, Mapping) else None
                    if isinstance(device_id, str) and state in {
                        "pending_dispatch", "dispatched_not_accepted", "accepted_unverified", "applied"
                    }:
                        leaf_ledger[device_id] = state
        enhanced["leaf_ledger"] = leaf_ledger
    return ContourApplyReceipt(
        operation_id=payload["operation_id"], request_id=payload["request_id"],
        correlation_id=payload.get("correlation_id"), contour_id=payload["contour_id"], context=context,
        status=status, room_count=payload["room_count"], command_count=payload["command_count"],
        accepted_count=payload["accepted_count"], confirmed_room_count=payload["confirmed_room_count"],
        temperature_changes=payload["changes"]["temperature"], strategy_changes=payload["changes"]["strategy"],
        automatic_mode_changes=payload["changes"]["automatic_mode"], reasons=tuple(payload["reasons"]),
        created_at=payload["created_at"], updated_at=payload["updated_at"], enhanced=enhanced,
    )
