from __future__ import annotations

from dataclasses import replace

from ..domain.ai_assistant_advisory import AiAdvisory, ai_advisory_from_payload
from ..domain.ai_assistant_state import AiAssistantState
from ..domain.ai_assistant_types import (
    AiAdvisoryStatus,
    AiAssistantViolation,
    AiProviderPreset,
)
from ..domain.ai_assistant_usage import AiUsageAggregate, AiUsageCall, AiUsageStats
from ..domain.ai_assistant_json import AiJsonObject, AiJsonValue


AI_ASSISTANT_STORAGE_VERSION = 1


def ai_assistant_state_to_payload(state: AiAssistantState) -> AiJsonObject:
    return {
        "version": AI_ASSISTANT_STORAGE_VERSION,
        "last_advisory": (
            None
            if state.last_advisory is None
            else _advisory_to_payload(state.last_advisory)
        ),
        "stats": {
            "aggregates": [_aggregate_to_payload(item) for item in state.stats.aggregates],
            "recent_calls": [_call_to_payload(item) for item in state.stats.recent_calls],
        },
    }


def ai_assistant_state_from_payload(payload: AiJsonValue) -> AiAssistantState:
    if not _exact_keys(payload, {"version", "last_advisory", "stats"}):
        raise AiAssistantViolation("invalid_ai_assistant_state")
    assert isinstance(payload, dict)
    if payload["version"] != AI_ASSISTANT_STORAGE_VERSION:
        raise AiAssistantViolation("invalid_ai_assistant_state")
    return AiAssistantState(
        last_advisory=_advisory_from_payload(payload["last_advisory"]),
        stats=_stats_from_payload(payload["stats"]),
    )


def _advisory_to_payload(advisory: AiAdvisory) -> AiJsonObject:
    return {
        "version": advisory.version,
        "source": advisory.source,
        "generated_at": advisory.generated_at,
        "status": advisory.status.value,
        "summary": advisory.summary,
        "recommendations": [
            _item_to_payload(item.code, item.priority, item.evidence, item.room_id)
            for item in advisory.recommendations
        ],
        "risk_flags": [
            _item_to_payload(item.code, item.severity, item.evidence, item.room_id)
            for item in advisory.risk_flags
        ],
    }


def _advisory_from_payload(value: AiJsonValue) -> AiAdvisory | None:
    if value is None:
        return None
    if not _exact_keys(
        value,
        {
            "version",
            "source",
            "generated_at",
            "status",
            "summary",
            "recommendations",
            "risk_flags",
        },
    ):
        raise AiAssistantViolation("invalid_ai_assistant_state")
    assert isinstance(value, dict)
    status_value = value["status"]
    if type(status_value) is not str:
        raise AiAssistantViolation("invalid_ai_assistant_state")
    try:
        status = AiAdvisoryStatus(status_value)
    except ValueError as error:
        raise AiAssistantViolation("invalid_ai_assistant_state") from error
    payload = {
        "version": value["version"],
        "source": value["source"],
        "generatedAt": value["generated_at"],
        "summary": {"code": value["summary"]},
        "recommendations": _external_items(value["recommendations"], "priority"),
        "riskFlags": _external_items(value["risk_flags"], "severity"),
    }
    return replace(ai_advisory_from_payload(payload), status=status)


def _item_to_payload(
    code: str,
    level: str,
    evidence: tuple[str, ...],
    room_id: str | None,
) -> AiJsonObject:
    payload: AiJsonObject = {
        "code": code,
        "evidence": list(evidence),
        "room_id": room_id,
    }
    if level in {"info", "warning"}:
        payload["priority"] = level
    return payload


def _external_items(value: AiJsonValue, level_key: str) -> list[AiJsonObject]:
    if type(value) is not list:
        raise AiAssistantViolation("invalid_ai_assistant_state")
    items: list[AiJsonObject] = []
    for item in value:
        if not _exact_keys(item, {"code", "evidence", "room_id", "priority"}):
            raise AiAssistantViolation("invalid_ai_assistant_state")
        assert isinstance(item, dict)
        level = item["priority"]
        if type(level) is not str:
            raise AiAssistantViolation("invalid_ai_assistant_state")
        items.append(
            {
                "code": item["code"],
                level_key: level,
                "evidence": item["evidence"],
                "roomId": item["room_id"],
            }
        )
    return items


def _aggregate_to_payload(aggregate: AiUsageAggregate) -> AiJsonObject:
    return {
        "preset": aggregate.preset.value,
        "model": aggregate.model,
        "calls": aggregate.calls,
        "successes": aggregate.successes,
        "auth_errors": aggregate.auth_errors,
        "http_errors": aggregate.http_errors,
        "timeout_errors": aggregate.timeout_errors,
        "invalid_errors": aggregate.invalid_errors,
        "prompt_tokens": aggregate.prompt_tokens,
        "completion_tokens": aggregate.completion_tokens,
        "latency_ms": aggregate.latency_ms,
    }


def _call_to_payload(call: AiUsageCall) -> AiJsonObject:
    return {
        "ts": call.ts,
        "preset": call.preset.value,
        "model": call.model,
        "status": call.status.value,
        "summary_code": call.summary_code,
        "prompt_tokens": call.prompt_tokens,
        "completion_tokens": call.completion_tokens,
        "latency_ms": call.latency_ms,
        "error_class": call.error_class,
    }


def _stats_from_payload(value: AiJsonValue) -> AiUsageStats:
    if not _exact_keys(value, {"aggregates", "recent_calls"}):
        raise AiAssistantViolation("invalid_ai_assistant_state")
    assert isinstance(value, dict)
    aggregates = value["aggregates"]
    recent_calls = value["recent_calls"]
    if type(aggregates) is not list or type(recent_calls) is not list:
        raise AiAssistantViolation("invalid_ai_assistant_state")
    return AiUsageStats(
        aggregates=tuple(_aggregate_from_payload(item) for item in aggregates),
        recent_calls=tuple(_call_from_payload(item) for item in recent_calls),
    )


def _aggregate_from_payload(value: AiJsonValue) -> AiUsageAggregate:
    fields = {
        "preset",
        "model",
        "calls",
        "successes",
        "auth_errors",
        "http_errors",
        "timeout_errors",
        "invalid_errors",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
    }
    if not _exact_keys(value, fields):
        raise AiAssistantViolation("invalid_ai_assistant_state")
    assert isinstance(value, dict)
    try:
        preset = AiProviderPreset(value["preset"])
    except (TypeError, ValueError) as error:
        raise AiAssistantViolation("invalid_ai_assistant_state") from error
    return AiUsageAggregate(preset=preset, **{key: value[key] for key in fields - {"preset"}})


def _call_from_payload(value: AiJsonValue) -> AiUsageCall:
    fields = {
        "ts",
        "preset",
        "model",
        "status",
        "summary_code",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "error_class",
    }
    if not _exact_keys(value, fields):
        raise AiAssistantViolation("invalid_ai_assistant_state")
    assert isinstance(value, dict)
    try:
        preset = AiProviderPreset(value["preset"])
        status = AiAdvisoryStatus(value["status"])
    except (TypeError, ValueError) as error:
        raise AiAssistantViolation("invalid_ai_assistant_state") from error
    return AiUsageCall(
        ts=value["ts"],
        preset=preset,
        model=value["model"],
        status=status,
        summary_code=value["summary_code"],
        prompt_tokens=value["prompt_tokens"],
        completion_tokens=value["completion_tokens"],
        latency_ms=value["latency_ms"],
        error_class=value["error_class"],
    )


def _exact_keys(value: AiJsonValue, expected: set[str]) -> bool:
    return type(value) is dict and set(value) == expected
