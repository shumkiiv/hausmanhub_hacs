from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from typing import Protocol, assert_never

from ..domain.ai_assistant import (
    AI_ADVISORY_VERSION,
    AiAdvisory,
    AiAdvisoryStatus,
    AiAssistantSettings,
    AiAssistantViolation,
    AiProviderPreset,
    AiRecommendation,
    AiRiskFlag,
    AiUsageCall,
    ai_advisory_from_payload,
)
from ..domain.ai_assistant_state import AiAssistantState
from ..domain.ai_assistant_json import AiJsonObject


_RETRY_DELAY_SECONDS = 0.25
_MAX_PROVIDER_CONTENT_LENGTH = 64 * 1024


@dataclass(frozen=True, slots=True)
class AiProviderCompletion:
    content: str
    prompt_tokens: int
    completion_tokens: int

    def __post_init__(self) -> None:
        if type(self.content) is not str or len(self.content) > _MAX_PROVIDER_CONTENT_LENGTH:
            raise AiAssistantViolation("invalid_provider_completion")
        if any(
            type(value) is not int or value < 0
            for value in (self.prompt_tokens, self.completion_tokens)
        ):
            raise AiAssistantViolation("invalid_provider_completion")


@dataclass(frozen=True, slots=True)
class AiProviderHttpError(RuntimeError):
    status: int

    def __str__(self) -> str:
        return f"provider HTTP {self.status}"


class AiProviderTimeout(TimeoutError):
    pass


class AiProviderUnavailable(RuntimeError):
    pass


class AiAssistantStateStorage(Protocol):
    async def async_load(self) -> AiAssistantState: ...

    async def async_save(self, state: AiAssistantState) -> None: ...


class AiProviderTransport(Protocol):
    async def async_complete(
        self,
        settings: AiAssistantSettings,
        api_key: str,
        evidence: AiJsonObject,
    ) -> AiProviderCompletion: ...

    async def async_complete_task(
        self,
        settings: AiAssistantSettings,
        api_key: str,
        system_prompt: str,
        payload: AiJsonObject,
    ) -> AiProviderCompletion: ...


class AiAssistantService:
    def __init__(
        self,
        *,
        settings: AiAssistantSettings | None,
        api_key: str | None,
        store: AiAssistantStateStorage,
        evidence_reader: Callable[[], Awaitable[AiJsonObject]],
        transport: AiProviderTransport,
        now_ms: Callable[[], int],
        monotonic_ms: Callable[[], int] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._api_key = api_key
        self._store = store
        self._evidence_reader = evidence_reader
        self._transport = transport
        self._now_ms = now_ms
        self._monotonic_ms = monotonic_ms or now_ms
        self._sleep = sleep
        self._state = AiAssistantState()
        self._lock = asyncio.Lock()

    async def async_start(self) -> None:
        async with self._lock:
            self._state = await self._store.async_load()

    async def async_state(self) -> AiAssistantState:
        async with self._lock:
            return self._state

    async def async_reset_state(self) -> None:
        """Clear stored assistant advice and usage without a provider call."""

        async with self._lock:
            state = AiAssistantState()
            await self._store.async_save(state)
            self._state = state

    async def async_refresh(self) -> AiAdvisory:
        async with self._lock:
            short_circuit = self._short_circuit_status()
            if short_circuit is not None:
                return await self._async_persist_advisory(
                    _failure_advisory(short_circuit, self._now_ms())
                )
            settings = self._settings
            api_key = self._api_key
            assert settings is not None
            assert api_key is not None
            evidence = await self._evidence_reader()
            return await self._async_refresh_configured(settings, api_key, evidence)

    @property
    def scenario_generation_available(self) -> bool:
        """Report whether the configured provider can generate a scenario draft."""

        return (
            self._settings is not None
            and self._settings.enabled
            and bool(self._api_key)
        )

    async def async_complete_json_task(
        self,
        *,
        system_prompt: str,
        payload: AiJsonObject,
    ) -> AiProviderCompletion:
        """Run one bounded JSON task without changing climate advisory state."""

        async with self._lock:
            if not self.scenario_generation_available:
                raise AiProviderUnavailable()
            settings = self._settings
            api_key = self._api_key
            assert settings is not None
            assert api_key is not None
            return await self._transport.async_complete_task(
                settings,
                api_key,
                system_prompt,
                payload,
            )

    def _short_circuit_status(self) -> AiAdvisoryStatus | None:
        if self._settings is None or not self._api_key:
            return AiAdvisoryStatus.UNCONFIGURED
        if not self._settings.enabled:
            return AiAdvisoryStatus.DISABLED
        return None

    async def _async_refresh_configured(
        self,
        settings: AiAssistantSettings,
        api_key: str,
        evidence: AiJsonObject,
    ) -> AiAdvisory:
        for attempt in range(2):
            started_at = self._monotonic_ms()
            try:
                completion = await self._transport.async_complete(
                    settings,
                    api_key,
                    evidence,
                )
                advisory = _advisory_from_completion(completion)
            except AiProviderTimeout:
                advisory = _failure_advisory(
                    AiAdvisoryStatus.PROVIDER_TIMEOUT,
                    self._now_ms(),
                )
                error_class = "timeout"
                retryable = True
            except AiProviderHttpError as error:
                advisory = _failure_advisory(
                    AiAdvisoryStatus.PROVIDER_ERROR,
                    self._now_ms(),
                )
                error_class = "auth" if error.status == 401 else "http"
                retryable = error.status != 401
            except AiProviderUnavailable:
                advisory = _failure_advisory(
                    AiAdvisoryStatus.PROVIDER_UNAVAILABLE,
                    self._now_ms(),
                )
                error_class = "http"
                retryable = True
            except (AiAssistantViolation, json.JSONDecodeError):
                advisory = _failure_advisory(
                    AiAdvisoryStatus.PROVIDER_OUTPUT_INVALID,
                    self._now_ms(),
                )
                error_class = "invalid"
                retryable = False
            else:
                return await self._async_persist_call(
                    settings,
                    advisory,
                    completion.prompt_tokens,
                    completion.completion_tokens,
                    _elapsed_ms(started_at, self._monotonic_ms()),
                    None,
                )
            await self._async_persist_call(
                settings,
                advisory,
                0,
                0,
                _elapsed_ms(started_at, self._monotonic_ms()),
                error_class,
            )
            if not retryable or attempt == 1:
                return advisory
            await self._sleep(_RETRY_DELAY_SECONDS)
        raise AssertionError("provider retry loop must return")

    async def _async_persist_call(
        self,
        settings: AiAssistantSettings,
        advisory: AiAdvisory,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
        error_class: str | None,
    ) -> AiAdvisory:
        state = AiAssistantState(
            last_advisory=advisory,
            stats=self._state.stats.with_call(
                AiUsageCall(
                    ts=self._now_ms(),
                    preset=settings.preset,
                    model=settings.model,
                    status=advisory.status,
                    summary_code=advisory.summary,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    latency_ms=latency_ms,
                    error_class=error_class,
                )
            ),
        )
        await self._store.async_save(state)
        self._state = state
        return advisory

    async def _async_persist_advisory(self, advisory: AiAdvisory) -> AiAdvisory:
        state = AiAssistantState(last_advisory=advisory, stats=self._state.stats)
        await self._store.async_save(state)
        self._state = state
        return advisory


def _advisory_from_completion(completion: AiProviderCompletion) -> AiAdvisory:
    payload = json.loads(completion.content)
    return ai_advisory_from_payload(payload)


def _failure_advisory(status: AiAdvisoryStatus, generated_at: int) -> AiAdvisory:
    evidence_code, risk_code = _failure_codes(status)
    return AiAdvisory(
        version=AI_ADVISORY_VERSION,
        source="hausman_hub",
        generated_at=generated_at,
        status=status,
        summary="evidence_limited",
        recommendations=(
            AiRecommendation(
                code="use_deterministic_evidence",
                priority="info",
                evidence=(evidence_code,),
            ),
        ),
        risk_flags=(
            AiRiskFlag(
                code=risk_code,
                severity="info",
                evidence=(evidence_code,),
            ),
        ),
    )


def _failure_codes(status: AiAdvisoryStatus) -> tuple[str, str]:
    match status:
        case AiAdvisoryStatus.DISABLED | AiAdvisoryStatus.UNCONFIGURED:
            return "provider_disabled_unconfigured", "provider_unavailable"
        case AiAdvisoryStatus.PROVIDER_UNAVAILABLE:
            return "provider_enabled_unconfigured", "provider_unavailable"
        case AiAdvisoryStatus.PROVIDER_TIMEOUT:
            return "provider_timeout", "provider_timeout"
        case AiAdvisoryStatus.PROVIDER_ERROR:
            return "provider_error", "provider_error"
        case AiAdvisoryStatus.PROVIDER_OUTPUT_INVALID:
            return "provider_output_invalid", "provider_output_invalid"
        case AiAdvisoryStatus.READY:
            raise AiAssistantViolation("invalid_failure_status")
        case unreachable:
            assert_never(unreachable)


def _elapsed_ms(started_at: int, finished_at: int) -> int:
    return max(0, finished_at - started_at)
