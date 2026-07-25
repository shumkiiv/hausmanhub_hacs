from __future__ import annotations

from dataclasses import dataclass, replace

from .ai_assistant_types import (
    AiAdvisoryStatus,
    AiAssistantViolation,
    AiProviderPreset,
)


_MAX_RECENT_CALLS = 150
_MAX_COUNTER = 9_007_199_254_740_991
_SUMMARY_CODES = frozenset({"advisory_available", "evidence_limited"})
_ERROR_CLASSES = frozenset({"auth", "http", "timeout", "invalid"})


@dataclass(frozen=True, slots=True)
class AiUsageCall:
    ts: int
    preset: AiProviderPreset
    model: str
    status: AiAdvisoryStatus
    summary_code: str | None
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    error_class: str | None

    def __post_init__(self) -> None:
        if type(self.ts) is not int or not 0 < self.ts <= _MAX_COUNTER:
            raise AiAssistantViolation("invalid_usage_call")
        if not isinstance(self.preset, AiProviderPreset) or not isinstance(
            self.status, AiAdvisoryStatus
        ):
            raise AiAssistantViolation("invalid_usage_call")
        if type(self.model) is not str or not 0 < len(self.model) <= 128:
            raise AiAssistantViolation("invalid_usage_call")
        if self.summary_code is not None and self.summary_code not in _SUMMARY_CODES:
            raise AiAssistantViolation("invalid_usage_call")
        if self.error_class is not None and self.error_class not in _ERROR_CLASSES:
            raise AiAssistantViolation("invalid_usage_call")
        if any(
            type(value) is not int or not 0 <= value <= _MAX_COUNTER
            for value in (
                self.prompt_tokens,
                self.completion_tokens,
                self.latency_ms,
            )
        ):
            raise AiAssistantViolation("invalid_usage_call")


@dataclass(frozen=True, slots=True)
class AiUsageAggregate:
    preset: AiProviderPreset
    model: str
    calls: int = 0
    successes: int = 0
    auth_errors: int = 0
    http_errors: int = 0
    timeout_errors: int = 0
    invalid_errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.preset, AiProviderPreset):
            raise AiAssistantViolation("invalid_usage_stats")
        if type(self.model) is not str or not 0 < len(self.model) <= 128:
            raise AiAssistantViolation("invalid_usage_stats")
        if any(
            type(value) is not int or not 0 <= value <= _MAX_COUNTER
            for value in (
                self.calls,
                self.successes,
                self.auth_errors,
                self.http_errors,
                self.timeout_errors,
                self.invalid_errors,
                self.prompt_tokens,
                self.completion_tokens,
                self.latency_ms,
            )
        ):
            raise AiAssistantViolation("invalid_usage_stats")

    def with_call(self, call: AiUsageCall) -> AiUsageAggregate:
        if (self.preset, self.model) != (call.preset, call.model):
            raise AiAssistantViolation("invalid_usage_call")
        update = {
            "calls": self.calls + 1,
            "prompt_tokens": self.prompt_tokens + call.prompt_tokens,
            "completion_tokens": self.completion_tokens + call.completion_tokens,
            "latency_ms": self.latency_ms + call.latency_ms,
        }
        if call.status is AiAdvisoryStatus.READY:
            update["successes"] = self.successes + 1
        elif call.error_class is not None:
            update[f"{call.error_class}_errors"] = getattr(
                self, f"{call.error_class}_errors"
            ) + 1
        return replace(self, **update)


@dataclass(frozen=True, slots=True)
class AiUsageStats:
    aggregates: tuple[AiUsageAggregate, ...] = ()
    recent_calls: tuple[AiUsageCall, ...] = ()

    def __post_init__(self) -> None:
        if type(self.aggregates) is not tuple or type(self.recent_calls) is not tuple:
            raise AiAssistantViolation("invalid_usage_stats")
        if len(self.recent_calls) > _MAX_RECENT_CALLS or any(
            not isinstance(item, AiUsageCall) for item in self.recent_calls
        ):
            raise AiAssistantViolation("invalid_usage_stats")
        if any(not isinstance(item, AiUsageAggregate) for item in self.aggregates):
            raise AiAssistantViolation("invalid_usage_stats")
        keys = tuple((item.preset, item.model) for item in self.aggregates)
        if len(keys) != len(set(keys)) or tuple(sorted(keys)) != keys:
            raise AiAssistantViolation("invalid_usage_stats")

    def with_call(self, call: AiUsageCall) -> AiUsageStats:
        key = (call.preset, call.model)
        existing = next(
            (item for item in self.aggregates if (item.preset, item.model) == key),
            None,
        )
        aggregate = (
            AiUsageAggregate(call.preset, call.model)
            if existing is None
            else existing
        )
        updated = tuple(
            item for item in self.aggregates if (item.preset, item.model) != key
        ) + (aggregate.with_call(call),)
        return AiUsageStats(
            aggregates=tuple(sorted(updated, key=lambda item: (item.preset, item.model))),
            recent_calls=(self.recent_calls + (call,))[-_MAX_RECENT_CALLS:],
        )
