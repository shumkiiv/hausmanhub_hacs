from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import json
import math
import re
import secrets
import time

from ..correlation import CorrelationIdError, validate_correlation_id
from ..climate_revision import is_control_revision
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
from .climate_application_models import ClimateTargetAxis


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


def _public_manual_reason(state: str) -> str:
    """Translate private frozen-manual states to the stable receipt contract."""

    return "external_off" if state == "manual_external_off" else "user_excluded"


def _public_manual_message(state: str) -> str:
    """Return the exact contract text for one manually retained owner."""

    return (
        "Устройство выключено вручную и исключено из контура."
        if state == "manual_external_off"
        else "Устройство исключено пользователем из автоматического контура."
    )


def _public_manual_message_code(state: str) -> str:
    return "external_off" if state == "manual_external_off" else "manual_excluded"


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

    @property
    def explicit_temperature_alignment(self) -> bool:
        return bool(self.native_plan.explicit_temperature_targets)

    @property
    def explicit_target_alignment(self) -> bool:
        """Whether this frozen plan carries an explicit physical target axis."""

        return bool(
            self.native_plan.explicit_temperature_targets
            or self.native_plan.explicit_humidity_targets
        )

    @property
    def explicit_temperature_targets(self) -> dict[str, float]:
        """Return the immutable operation's target facts for read-back only."""

        return dict(self.native_plan.explicit_temperature_targets)

    @property
    def explicit_humidity_targets(self) -> dict[str, int]:
        """Return immutable humidity target facts for read-back only."""

        return dict(self.native_plan.explicit_humidity_targets)

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
    # Private whole-home axis delta. The v1 public receipt deliberately has
    # no humidity field, but durable compatibility facts must not invent it.
    humidity_changes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.context, ClimateControlContext):
            raise ContourApplyViolation("climate control receipt context is invalid")
        if not isinstance(self.status, ContourApplyStatus):
            raise ContourApplyViolation("climate control receipt status is invalid")
        if any(reason not in _REASON_NAMES for reason in self.reasons):
            raise ContourApplyViolation("climate control receipt reason is invalid")
        if type(self.humidity_changes) is not int or self.humidity_changes < 0:
            raise ContourApplyViolation("climate control receipt humidity changes are invalid")

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


def _live_device_outcomes(
    enhanced: Mapping[str, object] | None,
) -> Mapping[str, Mapping[str, object]] | None:
    """Expose the frozen per-leaf executor boundary to the tablet adapter.

    Aggregate counts cannot prove a multi-device home command.  The ledger is
    checkpointed before and after every owner batch, so it is the only source
    for an exact live outcome map.  Confirmation remains the tablet's own
    fresh read-back proof; these leaves describe dispatch only.
    """

    if not isinstance(enhanced, Mapping):
        return None
    ledger = enhanced.get("leaf_ledger")
    if not isinstance(ledger, Mapping) or not ledger:
        return None
    outcomes: dict[str, dict[str, object]] = {}
    for device_id, state in ledger.items():
        if not isinstance(device_id, str):
            return None
        if state == "accepted_unverified":
            outcomes[device_id] = {
                "execution_state": state,
                "command_count": 1,
                "accepted_count": 1,
                "retry_policy": "forbidden_after_dispatch",
            }
        elif state in {"started", "dispatched_not_accepted"}:
            outcomes[device_id] = {
                "execution_state": "dispatched_not_accepted",
                "command_count": 1,
                "accepted_count": 0,
                "retry_policy": "forbidden_after_dispatch",
            }
        elif state == "blocked_before_dispatch":
            outcomes[device_id] = {
                "status": "not_attempted",
                "reason": "configuration_error",
                "execution_state": state,
                "message_code": "configuration_error",
                "command_count": 0,
                "accepted_count": 0,
            }
        elif state == "deferred_offline":
            outcomes[device_id] = {
                "status": "deferred",
                "reason": "device_unavailable",
                "message_code": "deferred_offline",
                "message": "Цель сохранена, устройство недоступно.",
                "command_count": 0,
                "accepted_count": 0,
            }
        elif state in {"manual_user_excluded", "manual_external_off"}:
            outcomes[device_id] = {
                "status": "manual",
                "reason": _public_manual_reason(state),
                "message_code": _public_manual_message_code(state),
                "message": _public_manual_message(state),
                "command_count": 0,
                "accepted_count": 0,
            }
        elif state == "already_in_sync":
            evidence = enhanced.get("already_in_sync_evidence")
            proof = evidence.get(device_id) if isinstance(evidence, Mapping) else None
            if not isinstance(proof, Mapping):
                return None
            outcomes[device_id] = {
                "status": "confirmed", "reason": "none",
                "execution_state": "already_in_sync", "message_code": "confirmed",
                "message": "Результат подтверждён чтением состояния.",
                "command_count": 0, "accepted_count": 0,
                "evidence": {
                    **dict(proof),
                    "action": {
                        "request_fingerprint": enhanced["request_fingerprint"],
                        "action": enhanced["action"],
                        "parameters": enhanced["parameters"],
                    },
                },
            }
        elif state == "applied":
            # The runtime reached this state only after a fresh native
            # read-back confirmed the frozen owner.  Surface that terminal
            # fact to the tablet coordinator instead of leaving a verified
            # physical leaf indistinguishable from an unresolved acceptance.
            outcomes[device_id] = {
                "status": "confirmed",
                "reason": "none",
                "execution_state": "applied",
                "message_code": "confirmed",
                "message": "Результат подтверждён чтением состояния.",
                "command_count": 1,
                "accepted_count": 1,
                "evidence": {
                    "native_read_back": True,
                    "fresh": True,
                },
            }
        else:
            return None
    return outcomes


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
        zero_call_proof_blocked = (
            not plan.strict_calls
            and plan.explicit_target_alignment
            and isinstance(enhanced, Mapping)
            and isinstance(enhanced.get("leaf_ledger"), Mapping)
            and any(
                state == "blocked_before_dispatch"
                for state in enhanced["leaf_ledger"].values()
            )
        )
        zero_call_terminal_partial = (
            not plan.strict_calls
            and isinstance(enhanced, Mapping)
            and isinstance(enhanced.get("leaf_ledger"), Mapping)
            and any(
                state in {"deferred_offline", "manual_user_excluded", "manual_external_off"}
                for state in enhanced["leaf_ledger"].values()
            )
        )
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
                    ContourApplyStatus.REJECTED
                    if zero_call_proof_blocked
                    else ContourApplyStatus.PARTIAL
                    if zero_call_terminal_partial
                    else (
                        ContourApplyStatus.PENDING
                        if plan.strict_calls
                        else ContourApplyStatus.CONFIRMED
                    )
                )
            ),
            room_count=len(plan.target_room_ids),
            command_count=len(plan.strict_calls),
            accepted_count=0,
            confirmed_room_count=(
                0
                if zero_call_proof_blocked
                else len(plan.native_plan.initially_aligned_room_ids)
            ),
            temperature_changes=plan.desired_state_changes.temperature,
            strategy_changes=plan.desired_state_changes.strategy,
            automatic_mode_changes=plan.desired_state_changes.automatic_mode,
            humidity_changes=plan.desired_state_changes.humidity,
            reasons=(
                ("engine_rejected",)
                if not plan.native_plan.preflight_permitted
                else (
                    ()
                    if plan.strict_calls or zero_call_terminal_partial
                    else (
                        ("state_not_confirmed",)
                        if zero_call_proof_blocked
                        else ("already_in_sync",)
                    )
                )
            ),
            created_at=now,
            updated_at=now,
            enhanced=enhanced,
            device_outcomes=(
                _live_device_outcomes(enhanced)
                if zero_call_terminal_partial
                else
                {
                    device_id: {
                        "status": "not_attempted",
                        "reason": "configuration_error",
                        "execution_state": "blocked_before_dispatch",
                        "message_code": "configuration_error",
                        "message": "Конфигурация устройства требует проверки.",
                        "command_count": 0,
                        "accepted_count": 0,
                    }
                    for device_id in enhanced["leaf_ledger"]
                }
                if zero_call_proof_blocked
                else (
                    {
                        device_id: (
                            {
                                "status": "deferred", "reason": "device_unavailable",
                                "message_code": "deferred_offline",
                                "message": "Цель сохранена, устройство недоступно.",
                                "command_count": 0, "accepted_count": 0,
                            }
                            if state == "deferred_offline" else {
                                "status": "manual", "reason": _public_manual_reason(state),
                                "message_code": _public_manual_message_code(state),
                                "message": _public_manual_message(state),
                                "command_count": 0, "accepted_count": 0,
                            }
                            if state in {"manual_user_excluded", "manual_external_off"} else {
                                "status": "confirmed", "reason": "none",
                                "execution_state": "already_in_sync",
                                "message_code": "confirmed",
                                "message": "Результат подтверждён чтением состояния.",
                                "command_count": 0, "accepted_count": 0,
                            }
                        )
                        for device_id, state in enhanced["leaf_ledger"].items()
                    }
                    if zero_call_terminal_partial
                    else (
                    {
                        device_id: {
                            "status": "confirmed", "reason": "none",
                            "execution_state": "already_in_sync",
                            "message_code": "confirmed",
                            "message": "Результат подтверждён чтением состояния.",
                            "command_count": 0, "accepted_count": 0,
                            "evidence": {
                                **dict(enhanced["already_in_sync_evidence"][device_id]),
                                "action": {
                                    "request_fingerprint": enhanced["request_fingerprint"],
                                    "action": enhanced["action"],
                                    "parameters": enhanced["parameters"],
                                },
                            },
                        }
                        for device_id in enhanced["leaf_ledger"]
                    }
                    if isinstance(enhanced, Mapping)
                    and enhanced.get("leaf_ledger")
                    and all(state == "already_in_sync" for state in enhanced["leaf_ledger"].values())
                    else None
                    )
                )
            ),
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
            device_outcomes=(
                _live_device_outcomes(record.enhanced)
                or record.receipt.device_outcomes
            ),
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

    def restore(
        self, records: object, *, authoritative_contour: ContourDefinition | None = None,
        authoritative_registry: ClimateRegistry | None = None,
    ) -> None:
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
            if (
                authoritative_contour is not None
                and authoritative_registry is not None
                and receipt.enhanced is not None
                and not _valid_authoritative_restored_scope(
                    receipt.enhanced["resolved_scope"], authoritative_contour,
                    authoritative_registry,
                )
            ):
                raise ContourApplyViolation("stored direct control scope is invalid")
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
        return max((value for value in values if is_control_revision(value)), default=0)

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
            not is_control_revision(expected_control_revision)
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
    explicit_temperature_alignment: bool = False,
    explicit_temperature_targets: Mapping[str, float] | None = None,
    explicit_humidity_targets: Mapping[str, int] | None = None,
    manual_device_ids: frozenset[str] = frozenset(),
) -> ContourApplyPlan:
    requested_temperature = (
        ClimateTargetAxis.TEMPERATURE in desired_state_changes.requested_axes
    )
    assignments = _selected_assignments(contour, room_ids)
    application_contour = _temperature_only_application_contour(
        contour,
        registry,
        target_room_ids=tuple(assignment.room_id for assignment in assignments),
        desired_state_changes=desired_state_changes,
        force_temperature_only=explicit_temperature_alignment or requested_temperature,
        requested_axes=desired_state_changes.requested_axes,
    )
    application_room_ids = frozenset(
        room.room_id for room in application_contour.rooms
    )
    assignments = tuple(
        assignment
        for assignment in assignments
        if assignment.room_id in application_room_ids
    )
    selected_room_ids = frozenset(
        assignment.room_id for assignment in assignments
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
            explicit_temperature_targets=(
                {
                    room_id: target
                    for room_id, target in explicit_temperature_targets.items()
                    if room_id in selected_room_ids
                }
                if explicit_temperature_targets is not None
                else (
                    {
                        assignment.room_id: assignment.target_temperature
                        for assignment in assignments
                    }
                    if explicit_temperature_alignment or requested_temperature
                    else None
                )
            ),
            explicit_humidity_targets=(
                {
                    room_id: target
                    for room_id, target in explicit_humidity_targets.items()
                    if room_id in selected_room_ids
                }
                if explicit_humidity_targets is not None
                else {
                    assignment.room_id: assignment.target_humidity
                    for assignment in assignments
                }
                if ClimateTargetAxis.HUMIDITY in desired_state_changes.requested_axes
                else None
            ),
            manual_device_ids=manual_device_ids,
            defer_stopped_target_owners=bool(
                desired_state_changes.requested_axes
            ),
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
    force_temperature_only: bool = False,
    requested_axes: frozenset[ClimateTargetAxis] = frozenset(),
) -> ContourDefinition:
    """Limit an explicit temperature operation to its actual actuator."""

    if (
        desired_state_changes.strategy != 0
        or desired_state_changes.automatic_mode != 0
        or (
            not force_temperature_only
        and desired_state_changes.temperature <= 0
        and not requested_axes
        )
    ):
        return contour
    targeted = frozenset(target_room_ids)
    rooms = []
    for room in contour.rooms:
        if room.room_id not in targeted:
            rooms.append(room)
            continue
        device_ids = tuple(
            device_id
            for device_id in room.device_ids
            if (
                (device := registry.device(device_id)) is not None
                and (
                    device.kind in {
                        ClimateDeviceKind.AIR_CONDITIONER,
                        ClimateDeviceKind.RADIATOR_THERMOSTAT,
                        ClimateDeviceKind.FLOOR_HEATING,
                    }
                    and (
                        not requested_axes
                        or ClimateTargetAxis.TEMPERATURE in requested_axes
                    )
                    or device.kind is ClimateDeviceKind.HUMIDIFIER
                    and ClimateTargetAxis.HUMIDITY in requested_axes
                )
            )
        )
        if device_ids:
            rooms.append(replace(room, device_ids=device_ids))
    return replace(contour, rooms=tuple(rooms))


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
    humidity_changes = 0
    strategy_changes = 0
    for assignment in assignments:
        prior = previous_rooms.get(assignment.room_id)
        if prior is None:
            raise ContourApplyViolation("previous climate room is unavailable")
        if not _same_number(prior.target_temperature, assignment.target_temperature):
            temperature_changes += 1
        if prior.target_humidity != assignment.target_humidity:
            humidity_changes += 1
        if prior.strategy is not assignment.strategy:
            strategy_changes += 1
    return ClimateDesiredStateChanges(
        temperature=temperature_changes,
        humidity=humidity_changes,
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
    action_code = metadata.get("action", action["code"])
    status = payload["status"]
    confirmed = status == ContourApplyStatus.CONFIRMED.value
    pending = status in {ContourApplyStatus.PENDING.value, ContourApplyStatus.PARTIAL.value}
    ledger = metadata.get("leaf_ledger", {})
    already_in_sync_evidence = metadata.get("already_in_sync_evidence", {})
    leaves: dict[str, dict[str, object]] = {}
    rooms: dict[str, dict[str, object]] = {}
    for row in scope["devices_by_room"]:  # type: ignore[index]
        room_id = row["room_id"]  # type: ignore[index]
        room_devices: dict[str, object] = {}
        for device_id in row["device_ids"]:  # type: ignore[index]
            actual = desired[device_id]  # type: ignore[index]
            leaf_state = ledger.get(device_id) if isinstance(ledger, Mapping) else None
            authoritative_evidence = (
                already_in_sync_evidence.get(device_id)
                if isinstance(already_in_sync_evidence, Mapping)
                else None
            )
            if (
                confirmed
                and payload["command_count"] == 0
                and isinstance(authoritative_evidence, Mapping)
                and authoritative_evidence.get("fresh") is True
            ):
                evidence = dict(authoritative_evidence)
                evidence["action"] = {
                    "request_fingerprint": fingerprint,
                    "action": action_code,
                    "parameters": action_parameters,
                }
                leaf = {
                    "status": "confirmed", "reason": "none",
                    "execution_state": "already_in_sync",
                    "message_code": "confirmed",
                    "message": "Результат подтверждён чтением состояния.",
                    "command_count": 0, "accepted_count": 0,
                    "evidence": evidence,
                }
            elif leaf_state == "blocked_before_dispatch":
                leaf = {
                    "status": "not_attempted", "reason": "configuration_error",
                    "execution_state": "blocked_before_dispatch",
                    "message_code": "configuration_error",
                    "message": "Конфигурация устройства требует проверки.",
                    "command_count": 0, "accepted_count": 0,
                }
            elif leaf_state == "deferred_offline":
                leaf = {
                    "status": "deferred", "reason": "device_unavailable",
                    "message_code": "deferred_offline",
                    "message": "Цель сохранена, устройство недоступно.",
                    "command_count": 0, "accepted_count": 0,
                }
            elif leaf_state in {"manual_user_excluded", "manual_external_off"}:
                leaf = {
                    "status": "manual", "reason": _public_manual_reason(leaf_state),
                    "message_code": _public_manual_message_code(leaf_state),
                    "message": _public_manual_message(leaf_state),
                    "command_count": 0, "accepted_count": 0,
                }
            elif leaf_state == "pending_dispatch":
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
                        "action": {"request_fingerprint": fingerprint, "action": action_code, "parameters": action_parameters},
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
        if all(
            device.get("execution_state") == "blocked_before_dispatch"
            for device in room_devices.values()
        ):
            room_status = {"status": "not_attempted", "reason": "configuration_error", "execution_state": "blocked_before_dispatch", "message_code": "configuration_error", "message": "Конфигурация устройства требует проверки.", "devices": room_devices}
        elif all(device.get("status") == "deferred" for device in room_devices.values()):
            room_status = {"status": "deferred", "reason": "device_unavailable", "message_code": "deferred_offline", "message": "Часть устройств недоступна.", "devices": room_devices}
        elif all(device.get("status") == "manual" for device in room_devices.values()):
            manual_reason = (
                "external_off"
                if all(device.get("reason") == "external_off" for device in room_devices.values())
                else "user_excluded"
            )
            room_status = {"status": "manual", "reason": manual_reason, "message_code": "external_off" if manual_reason == "external_off" else "manual_excluded", "message": "Устройства оставлены в ручном управлении.", "devices": room_devices}
        elif confirmed:
            room_status = {"status": "confirmed", "reason": "none", "execution_state": "applied", "message_code": "confirmed", "message": "Результат подтверждён чтением состояния.", "devices": room_devices}
        elif pending:
            execution_state = (
                "pending_dispatch"
                if room_devices and all(
                    device.get("execution_state") == "pending_dispatch"
                    for device in room_devices.values()
                )
                else "accepted_unverified"
            )
            room_status = {"status": "pending", "reason": "none", "execution_state": execution_state, "message_code": "pending", "message": "Команда принята и ожидает отправки.", "devices": room_devices}
        else:
            room_status = {"status": "partial", "reason": "none", "message_code": "partial", "message": "Результаты устройств различаются.", "devices": room_devices}
        rooms[room_id] = room_status
    message_code = {"confirmed": "confirmed", "partial": "partial", "pending": "pending", "rejected": "rejected", "unavailable": "unavailable"}[status]  # type: ignore[index]
    message = {"confirmed": "Результат подтверждён чтением состояния.", "partial": "Цель сохранена, часть устройств ожидает применения.", "pending": "Команда принята и ожидает подтверждения.", "rejected": "Команда отклонена.", "unavailable": "Результат команды пока недоступен."}[status]  # type: ignore[index]
    terminal_deferred = bool(leaves) and all(
        leaf.get("status") in {"deferred", "manual", "confirmed"}
        for leaf in leaves.values()
    ) and any(leaf.get("status") in {"deferred", "manual"} for leaf in leaves.values())
    if terminal_deferred:
        status = "partial"
        message_code = "partial"
        message = "Цель сохранена, часть устройств отложена или оставлена вручную."
    return {
        "duplicate": False, "action_snapshot": {"kind": metadata["kind"], "request_fingerprint": fingerprint,
            "action": action_code, "parameters": action_parameters, "resolved_scope": scope, "control_revision": expected},
        "desired_snapshot": desired, "desired_snapshot_fingerprint": metadata["desired_snapshot_fingerprint"],
        "expected_control_revision": expected, "resulting_control_revision": resulting,
        "message_code": message_code, "message": message, "request_fingerprint": fingerprint,
        "confirmation_window_ms": 8000, "final": terminal_deferred or not pending,
        "unfinished_device_count": 0 if confirmed or status == "rejected" or terminal_deferred else len(leaves),
        "read_back": {"attempted": payload["accepted"], "matched": confirmed, "observed_at": payload["updated_at"] if payload["accepted"] else None,
           "room_count": payload["room_count"], "confirmed_room_count": payload["confirmed_room_count"],
           "evidence": _enhanced_evidence(action, payload)},
        "intent": {"status": ("saved_deferred_offline" if any(leaf.get("status") == "deferred" for leaf in leaves.values()) else "saved_for_manual_device" if any(leaf.get("status") == "manual" for leaf in leaves.values()) else "saved_and_applied" if confirmed else ("saved_pending_confirmation" if pending else ("saved_blocked_before_dispatch" if status == "rejected" else "unsaved_unavailable"))),
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


def _canonical_receipt_fingerprint(value: object) -> str:
    """Return the one canonical digest dialect used by reliable receipts."""

    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("ascii")
    ).hexdigest()


def _valid_restored_action_snapshot(
    action_snapshot: Mapping[str, object],
    context: ClimateControlContext,
) -> bool:
    """Bind a frozen reliable action to its public contour context."""

    if set(action_snapshot) != {
        "kind", "request_fingerprint", "action", "parameters",
        "resolved_scope", "control_revision",
    }:
        return False
    action = action_snapshot.get("action")
    parameters = action_snapshot.get("parameters")
    if not isinstance(action, str) or not isinstance(parameters, Mapping):
        return False
    if context.action is ClimateControlAction.APPLY_SCHEDULE_PROFILE:
        native_parameters: dict[str, object] = {
            "contour_id": "climate", "confirm": True,
            "schedule_profile": context.profile.value,
        }
        expected_kind = "contour_apply"
    elif context.action is ClimateControlAction.APPLY_SAVED_SETTINGS:
        native_parameters = {"contour_id": "climate", "confirm": True}
        expected_kind = "contour_apply"
    else:
        native_parameters = {
            "contour_id": "climate", "confirm": True,
            "room_id": context.room_id,
            "action": (
                "clear" if context.action is ClimateControlAction.RETURN_TO_SCHEDULE
                else "set"
            ),
            "target_temperature": (
                None
                if context.action is ClimateControlAction.RETURN_TO_SCHEDULE
                else context.target_temperature
            ),
        }
        expected_kind = (
            "temporary_clear"
            if context.action is ClimateControlAction.RETURN_TO_SCHEDULE
            else "temporary_set"
        )
    if action_snapshot.get("kind") != expected_kind:
        return False
    if action == context.action.value:
        return dict(parameters) == native_parameters
    # A trusted reserved Tablet request deliberately retains its external
    # identity while the native contour context remains the actual operation.
    # Only the two translated temporary actions have that representation.
    if context.action is ClimateControlAction.SET_TEMPORARY_TEMPERATURE:
        return (
            action == "set_room_target"
            and dict(parameters) == {
                "target_temperature": context.target_temperature,
            }
        )
    if context.action is ClimateControlAction.RETURN_TO_SCHEDULE:
        return action == "clear_room_override" and dict(parameters) == {}
    if context.action is ClimateControlAction.APPLY_SAVED_SETTINGS:
        # A whole-home tablet target uses the native saved-settings context
        # while retaining its own public action identity across restart.
        return (
            action == "set_home_targets"
            and set(parameters) in ({"target_temperature"}, {"target_humidity"},
                                    {"target_temperature", "target_humidity"})
            and all(
                (key == "target_temperature" and _is_finite_snapshot_number(value))
                or (key == "target_humidity" and type(value) is int)
                for key, value in parameters.items()
            )
        )
    return False


def _restored_intent_temperature(
    context: ClimateControlContext, action_snapshot: Mapping[str, object],
) -> object:
    parameters = action_snapshot.get("parameters")
    if (
        context.action is ClimateControlAction.APPLY_SAVED_SETTINGS
        and action_snapshot.get("action") == "set_home_targets"
        and isinstance(parameters, Mapping)
    ):
        return parameters.get("target_temperature")
    return context.target_temperature


def _restored_intent_humidity(
    context: ClimateControlContext, action_snapshot: Mapping[str, object],
) -> object:
    parameters = action_snapshot.get("parameters")
    if (
        context.action is ClimateControlAction.APPLY_SAVED_SETTINGS
        and action_snapshot.get("action") == "set_home_targets"
        and isinstance(parameters, Mapping)
    ):
        return parameters.get("target_humidity")
    return None


def _direct_receipt_request_fingerprint(
    *, request_id: object, correlation_id: object,
    context: ClimateControlContext, scope: Mapping[str, object],
    expected_control_revision: int,
) -> str:
    """Rebuild the runtime's direct reliable-control identity exactly."""

    if context.action is ClimateControlAction.APPLY_SCHEDULE_PROFILE:
        parameters: dict[str, object] = {
            "contour_id": "climate", "confirm": True,
            "schedule_profile": context.profile.value,
        }
    elif context.action is ClimateControlAction.APPLY_SAVED_SETTINGS:
        parameters = {"contour_id": "climate", "confirm": True}
    else:
        parameters = {
            "contour_id": "climate", "confirm": True,
            "room_id": context.room_id,
            "action": (
                "clear" if context.action is ClimateControlAction.RETURN_TO_SCHEDULE
                else "set"
            ),
            "target_temperature": (
                None
                if context.action is ClimateControlAction.RETURN_TO_SCHEDULE
                else context.target_temperature
            ),
        }
    return _canonical_receipt_fingerprint({
        "request_id": request_id, "correlation_id": correlation_id,
        "reliability_profile": "climate_reliability_v1",
        "expected_control_revision": expected_control_revision,
        "action": context.action.value, "parameters": parameters,
        "scope": scope,
    })


def _is_finite_snapshot_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _valid_restored_desired_snapshot(
    desired: Mapping[str, object], scope: Mapping[str, object],
    context: ClimateControlContext,
) -> bool:
    """Check that reliable per-device intent is exactly the frozen scope."""

    if set(scope) != {"room_ids", "device_ids", "devices_by_room"}:
        return False
    room_ids = scope.get("room_ids")
    device_ids = scope.get("device_ids")
    rows = scope.get("devices_by_room")
    if (
        not isinstance(room_ids, list)
        or not isinstance(device_ids, list)
        or not isinstance(rows, list)
        or any(not isinstance(room_id, str) or _STABLE_ID.fullmatch(room_id) is None for room_id in room_ids)
        or any(not isinstance(device_id, str) or _STABLE_ID.fullmatch(device_id) is None for device_id in device_ids)
        or len(set(room_ids)) != len(room_ids)
        or len(set(device_ids)) != len(device_ids)
        or set(desired) != set(device_ids)
    ):
        return False
    row_devices: list[str] = []
    row_room_ids: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"room_id", "device_ids"}:
            return False
        row_room_id = row.get("room_id")
        row_device_ids = row.get("device_ids")
        if (
            not isinstance(row_room_id, str)
            or row_room_id not in room_ids
            or not isinstance(row_device_ids, list)
            or not row_device_ids
        ):
            return False
        row_room_ids.append(row_room_id)
        row_devices.extend(row_device_ids)
    if (
        len(set(row_room_ids)) != len(row_room_ids)
        or len(set(row_devices)) != len(row_devices)
        or row_devices != device_ids
    ):
        return False
    required_desired_keys = {
        "target_temperature", "target_humidity", "minimum_temperature",
        "target_strategy", "mode", "state", "override_state",
        "synchronization", "resulting_target_temperature",
    }
    for device_id in device_ids:
        value = desired.get(device_id)
        if not isinstance(value, Mapping) or set(value) != required_desired_keys:
            return False
        if (
            not _is_finite_snapshot_number(value.get("target_temperature"))
            or (
                value.get("minimum_temperature") is not None
                and not _is_finite_snapshot_number(value.get("minimum_temperature"))
            )
            or not _is_finite_snapshot_number(value.get("resulting_target_temperature"))
            or (
                value.get("target_humidity") is not None
                and not _is_finite_snapshot_number(value.get("target_humidity"))
            )
            or not isinstance(value.get("target_strategy"), str)
            or value.get("mode") != "automatic"
            or value.get("state") is not None
            or value.get("synchronization") is not None
            or value.get("override_state") not in {"active", "cleared"}
        ):
            return False
        if context.action is ClimateControlAction.SET_TEMPORARY_TEMPERATURE and (
            value.get("target_temperature") != context.target_temperature
            or value.get("resulting_target_temperature") != context.target_temperature
        ):
            return False
        if context.action is ClimateControlAction.RETURN_TO_SCHEDULE and (
            value.get("target_temperature") != context.target_temperature
            or value.get("resulting_target_temperature") != context.target_temperature
            or value.get("override_state") != "cleared"
        ):
            return False
    return True


def _valid_authoritative_restored_scope(
    scope: object, contour: ContourDefinition, registry: ClimateRegistry,
) -> bool:
    """Accept only an unsigned frozen scope that the active contour owns."""

    if not isinstance(scope, Mapping):
        return False
    rooms = {room.room_id: room for room in contour.rooms}
    devices = {device.device_id: device for device in registry.devices}
    room_ids = scope.get("room_ids")
    rows = scope.get("devices_by_room")
    if not isinstance(room_ids, list) or not isinstance(rows, list):
        return False
    if any(room_id not in rooms for room_id in room_ids):
        return False
    for row in rows:
        if not isinstance(row, Mapping):
            return False
        room_id = row.get("room_id")
        device_ids = row.get("device_ids")
        if room_id not in rooms or not isinstance(device_ids, list):
            return False
        if any(
            device_id not in devices
            or devices[device_id].room_id != room_id
            or device_id not in rooms[room_id].device_ids
            for device_id in device_ids
        ):
            return False
    return True


def _receipt_from_stored_payload(
    payload: dict[str, object], context: ClimateControlContext,
) -> ContourApplyReceipt:
    """Validate the immutable public fields needed for restart-safe polling."""

    status = ContourApplyStatus(payload["status"])
    enhanced = None
    enhanced_keys = {
        "duplicate", "action_snapshot", "desired_snapshot",
        "desired_snapshot_fingerprint", "expected_control_revision",
        "resulting_control_revision", "message_code", "request_fingerprint",
        "final", "unfinished_device_count", "intent", "outcomes",
    }
    present_enhanced_keys = enhanced_keys & set(payload)
    if present_enhanced_keys and present_enhanced_keys != enhanced_keys:
        raise ValueError("enhanced receipt is incomplete")
    if present_enhanced_keys:
        action_snapshot = payload["action_snapshot"]
        intent = payload["intent"]
        if (
            not isinstance(action_snapshot, Mapping)
            or not isinstance(intent, Mapping)
            or set(intent) != {
                "status", "request_fingerprint", "control_revision",
                "scope_revision", "scope_fingerprint", "resolved_scope",
                "desired_target_temperature", "desired_target_humidity",
            }
            or payload.get("action") != context.as_payload()
        ):
            raise ValueError("enhanced receipt is invalid")
        expected_revision = payload.get("expected_control_revision")
        resulting_revision = payload.get("resulting_control_revision")
        request_fingerprint = payload.get("request_fingerprint")
        scope = action_snapshot.get("resolved_scope")
        desired = payload.get("desired_snapshot")
        if (
            not isinstance(request_fingerprint, str)
            or re.fullmatch(r"[a-f0-9]{64}", request_fingerprint) is None
            or not isinstance(scope, Mapping)
            or not isinstance(desired, Mapping)
            or not _valid_restored_action_snapshot(action_snapshot, context)
            or not _valid_restored_desired_snapshot(desired, scope, context)
            or not is_control_revision(expected_revision)
            or not is_control_revision(resulting_revision)
            or resulting_revision != expected_revision + 1
            or action_snapshot.get("control_revision") != expected_revision
            or action_snapshot.get("request_fingerprint") != request_fingerprint
            or intent.get("request_fingerprint") != request_fingerprint
            or intent.get("control_revision") != resulting_revision
            or intent.get("scope_revision") != resulting_revision
            or intent.get("resolved_scope") != scope
            or intent.get("scope_fingerprint") != _canonical_receipt_fingerprint(scope)
            or intent.get("desired_target_temperature") != _restored_intent_temperature(
                context, action_snapshot
            )
            or intent.get("desired_target_humidity") != _restored_intent_humidity(
                context, action_snapshot
            )
            or payload.get("desired_snapshot_fingerprint")
            != _canonical_receipt_fingerprint(desired)
        ):
            raise ValueError("enhanced receipt revision is invalid")
        if context.room_id is not None and scope.get("room_ids") != [context.room_id]:
            raise ValueError("enhanced receipt scope is invalid")
        if action_snapshot["action"] == context.action.value:
            expected_fingerprint = _direct_receipt_request_fingerprint(
                request_id=payload.get("request_id"),
                correlation_id=payload.get("correlation_id"),
                context=context,
                scope=scope,
                expected_control_revision=expected_revision,
            )
            if request_fingerprint != expected_fingerprint:
                raise ValueError("enhanced receipt request fingerprint is invalid")
        enhanced = {
            "kind": action_snapshot["kind"], "request_fingerprint": action_snapshot["request_fingerprint"],
            "action": action_snapshot["action"], "parameters": dict(action_snapshot["parameters"]), "resolved_scope": dict(action_snapshot["resolved_scope"]),
            "desired_snapshot": dict(payload["desired_snapshot"]),
            "desired_snapshot_fingerprint": payload["desired_snapshot_fingerprint"],
            "scope_fingerprint": intent["scope_fingerprint"],
            "expected_control_revision": expected_revision,
            "resulting_control_revision": resulting_revision,
            "desired_target_temperature": intent["desired_target_temperature"],
            "desired_target_humidity": intent["desired_target_humidity"],
        }
        outcomes = payload.get("outcomes")
        rooms = outcomes.get("rooms") if isinstance(outcomes, Mapping) else None
        leaf_ledger: dict[str, str] = {}
        already_in_sync_evidence: dict[str, dict[str, object]] = {}
        if isinstance(rooms, Mapping):
            for room in rooms.values():
                devices = room.get("devices") if isinstance(room, Mapping) else None
                if not isinstance(devices, Mapping):
                    continue
                for device_id, leaf in devices.items():
                    state = leaf.get("execution_state") if isinstance(leaf, Mapping) else None
                    if (
                        isinstance(device_id, str)
                        and isinstance(leaf, Mapping)
                        and leaf.get("status") == "manual"
                        and leaf.get("reason") in {
                            "user_excluded", "external_off",
                        }
                    ):
                        leaf_ledger[device_id] = (
                            "manual_external_off"
                            if leaf["reason"] == "external_off"
                            else "manual_user_excluded"
                        )
                        continue
                    if (
                        isinstance(device_id, str)
                        and isinstance(leaf, Mapping)
                        and leaf.get("status") == "deferred"
                        and leaf.get("reason") == "device_unavailable"
                    ):
                        leaf_ledger[device_id] = "deferred_offline"
                        continue
                    if isinstance(device_id, str) and state in {
                        "pending_dispatch", "dispatched_not_accepted", "accepted_unverified",
                        "applied", "already_in_sync", "blocked_before_dispatch",
                    }:
                        leaf_ledger[device_id] = state
                        if state == "already_in_sync":
                            evidence = leaf.get("evidence")
                            if isinstance(evidence, Mapping):
                                already_in_sync_evidence[device_id] = dict(evidence)
        enhanced["leaf_ledger"] = leaf_ledger
        enhanced["already_in_sync_evidence"] = already_in_sync_evidence
        expected_intent_status = (
            "saved_deferred_offline"
            if any(state == "deferred_offline" for state in leaf_ledger.values())
            else "saved_for_manual_device"
            if any(state in {"manual_user_excluded", "manual_external_off"} for state in leaf_ledger.values())
            else "saved_and_applied" if status is ContourApplyStatus.CONFIRMED
            else "saved_pending_confirmation" if status is ContourApplyStatus.PENDING
            else "saved_blocked_before_dispatch" if status is ContourApplyStatus.REJECTED
            else "unsaved_unavailable"
        )
        if intent.get("status") != expected_intent_status:
            raise ValueError("enhanced receipt intent is invalid")
    return ContourApplyReceipt(
        operation_id=payload["operation_id"], request_id=payload["request_id"],
        correlation_id=payload.get("correlation_id"), contour_id=payload["contour_id"], context=context,
        status=status, room_count=payload["room_count"], command_count=payload["command_count"],
        accepted_count=payload["accepted_count"], confirmed_room_count=payload["confirmed_room_count"],
        temperature_changes=payload["changes"]["temperature"], strategy_changes=payload["changes"]["strategy"],
        automatic_mode_changes=payload["changes"]["automatic_mode"], reasons=tuple(payload["reasons"]),
        created_at=payload["created_at"], updated_at=payload["updated_at"], enhanced=enhanced,
    )
