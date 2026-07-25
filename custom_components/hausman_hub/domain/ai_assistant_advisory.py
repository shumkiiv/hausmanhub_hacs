from __future__ import annotations

from dataclasses import dataclass
import re

from .ai_assistant_types import (
    AI_ADVISORY_VERSION,
    MAX_TIMESTAMP,
    SUMMARY_CODES,
    AiAdvisoryStatus,
    AiAssistantViolation,
)
from .ai_assistant_json import AiJsonObject, AiJsonValue


_MAX_EVIDENCE = 8
_MAX_ITEMS = 32
_ROOM_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$", re.IGNORECASE)
_RECOMMENDATION_CODES = frozenset(
    {
        "use_deterministic_evidence",
        "review_temperature_gap",
        "refresh_evidence",
        "verify_physical_feedback",
        "verify_window_state",
        "inspect_state_mismatch",
    }
)
_RISK_CODES = frozenset(
    {
        "provider_unavailable",
        "provider_timeout",
        "provider_error",
        "provider_output_invalid",
        "temperature_outside_comfort_band",
        "stale_state",
        "physical_feedback_unconfirmed",
        "window_not_confirmed_closed",
        "state_mismatch",
    }
)
_EVIDENCE_CODES = frozenset(
    {
        "provider_disabled_unconfigured",
        "provider_enabled_unconfigured",
        "provider_timeout",
        "provider_error",
        "provider_output_invalid",
        "temperature_above_comfort",
        "temperature_below_comfort",
        "room_state_stale",
        "physical_feedback_not_confirmed",
        "window_not_closed",
        "observed_state_differs",
    }
)


@dataclass(frozen=True, slots=True)
class AiRecommendation:
    code: str
    priority: str
    evidence: tuple[str, ...]
    room_id: str | None = None

    def __post_init__(self) -> None:
        validate_item(
            self.code,
            self.priority,
            self.evidence,
            self.room_id,
            _RECOMMENDATION_CODES,
        )


@dataclass(frozen=True, slots=True)
class AiRiskFlag:
    code: str
    severity: str
    evidence: tuple[str, ...]
    room_id: str | None = None

    def __post_init__(self) -> None:
        validate_item(
            self.code,
            self.severity,
            self.evidence,
            self.room_id,
            _RISK_CODES,
        )


@dataclass(frozen=True, slots=True)
class AiAdvisory:
    version: int
    source: str
    generated_at: int
    status: AiAdvisoryStatus
    summary: str
    recommendations: tuple[AiRecommendation, ...]
    risk_flags: tuple[AiRiskFlag, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != AI_ADVISORY_VERSION:
            raise AiAssistantViolation("invalid_advisory_payload")
        if type(self.source) is not str or self.source not in {
            "provider",
            "hausman_hub",
        }:
            raise AiAssistantViolation("invalid_advisory_payload")
        if not valid_timestamp(self.generated_at) or not isinstance(
            self.status, AiAdvisoryStatus
        ):
            raise AiAssistantViolation("invalid_advisory_payload")
        if type(self.summary) is not str or self.summary not in SUMMARY_CODES:
            raise AiAssistantViolation("invalid_advisory_payload")
        if type(self.recommendations) is not tuple or len(self.recommendations) > _MAX_ITEMS:
            raise AiAssistantViolation("invalid_advisory_payload")
        if type(self.risk_flags) is not tuple or len(self.risk_flags) > _MAX_ITEMS:
            raise AiAssistantViolation("invalid_advisory_payload")
        if any(not isinstance(item, AiRecommendation) for item in self.recommendations):
            raise AiAssistantViolation("invalid_advisory_payload")
        if any(not isinstance(item, AiRiskFlag) for item in self.risk_flags):
            raise AiAssistantViolation("invalid_advisory_payload")


def ai_advisory_from_payload(payload: AiJsonValue) -> AiAdvisory:
    if not exact_keys(
        payload,
        {"version", "source", "generatedAt", "summary", "recommendations", "riskFlags"},
    ):
        raise AiAssistantViolation("invalid_advisory_payload")
    assert isinstance(payload, dict)
    summary = payload["summary"]
    if not exact_keys(summary, {"code"}):
        raise AiAssistantViolation("invalid_advisory_payload")
    assert isinstance(summary, dict)
    version = payload["version"]
    source = payload["source"]
    generated_at = payload["generatedAt"]
    summary_code = summary["code"]
    if not all(
        (
            type(version) is int,
            type(source) is str,
            type(generated_at) is int,
            type(summary_code) is str,
        )
    ):
        raise AiAssistantViolation("invalid_advisory_payload")
    return AiAdvisory(
        version=version,
        source=source,
        generated_at=generated_at,
        status=AiAdvisoryStatus.READY,
        summary=summary_code,
        recommendations=recommendations_from_payload(payload["recommendations"]),
        risk_flags=risk_flags_from_payload(payload["riskFlags"]),
    )


def validate_item(
    code: str,
    level: str,
    evidence: tuple[str, ...],
    room_id: str | None,
    allowed_codes: frozenset[str],
) -> None:
    if (
        type(code) is not str
        or type(level) is not str
        or code not in allowed_codes
        or level not in {"info", "warning"}
    ):
        raise AiAssistantViolation("invalid_advisory_payload")
    if room_id is not None and (
        type(room_id) is not str or _ROOM_ID.fullmatch(room_id) is None
    ):
        raise AiAssistantViolation("invalid_advisory_payload")
    if type(evidence) is not tuple or not 0 < len(evidence) <= _MAX_EVIDENCE:
        raise AiAssistantViolation("invalid_advisory_payload")
    if any(type(code) is not str or code not in _EVIDENCE_CODES for code in evidence):
        raise AiAssistantViolation("invalid_advisory_payload")


def recommendations_from_payload(value: AiJsonValue) -> tuple[AiRecommendation, ...]:
    return tuple(
        AiRecommendation(
            code=item_string(item, "code"),
            priority=item_string(item, "priority"),
            evidence=item_evidence(item),
            room_id=item_room_id(item),
        )
        for item in items(
            value,
            {"code", "priority", "evidence", "roomId"},
            {"code", "priority", "evidence"},
        )
    )


def risk_flags_from_payload(value: AiJsonValue) -> tuple[AiRiskFlag, ...]:
    return tuple(
        AiRiskFlag(
            code=item_string(item, "code"),
            severity=item_string(item, "severity"),
            evidence=item_evidence(item),
            room_id=item_room_id(item),
        )
        for item in items(
            value,
            {"code", "severity", "evidence", "roomId"},
            {"code", "severity", "evidence"},
        )
    )


def items(
    value: AiJsonValue,
    allowed_keys: set[str],
    required_keys: set[str],
) -> tuple[AiJsonObject, ...]:
    if type(value) is not list or len(value) > _MAX_ITEMS:
        raise AiAssistantViolation("invalid_advisory_payload")
    parsed = tuple(value)
    if any(not exact_keys(item, allowed_keys, required_keys) for item in parsed):
        raise AiAssistantViolation("invalid_advisory_payload")
    return parsed


def item_string(item: AiJsonObject, field: str) -> str:
    value = item[field]
    if type(value) is not str:
        raise AiAssistantViolation("invalid_advisory_payload")
    return value


def item_evidence(item: AiJsonObject) -> tuple[str, ...]:
    value = item["evidence"]
    if type(value) is not list or any(type(code) is not str for code in value):
        raise AiAssistantViolation("invalid_advisory_payload")
    return tuple(value)


def item_room_id(item: AiJsonObject) -> str | None:
    value = item.get("roomId")
    if value is not None and type(value) is not str:
        raise AiAssistantViolation("invalid_advisory_payload")
    return value


def exact_keys(
    value: AiJsonValue,
    allowed: set[str],
    required: set[str] | None = None,
) -> bool:
    return (
        type(value) is dict
        and set(value).issubset(allowed)
        and (required or allowed).issubset(value)
    )


def valid_timestamp(value: AiJsonValue) -> bool:
    return type(value) is int and 0 < value <= MAX_TIMESTAMP
