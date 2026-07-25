from __future__ import annotations

import asyncio
import json
import unittest

from custom_components.hausman_hub.application.ai_assistant import (
    AiAssistantService,
    AiProviderCompletion,
    AiProviderHttpError,
    AiProviderTimeout,
)
from custom_components.hausman_hub.domain.ai_assistant import (
    AiAdvisoryStatus,
    AiAssistantSettings,
    AiProviderPreset,
)
from custom_components.hausman_hub.domain.ai_assistant_state import AiAssistantState


NOW = 1_800_000_000_000


def provider_payload() -> dict[str, object]:
    return {
        "version": 1,
        "source": "provider",
        "generatedAt": NOW,
        "summary": {"code": "advisory_available"},
        "recommendations": [],
        "riskFlags": [],
    }


def settings(*, enabled: bool = True) -> AiAssistantSettings:
    return AiAssistantSettings(
        enabled=enabled,
        preset=AiProviderPreset.CUSTOM,
        base_url="https://provider.example/v1",
        model="advisory-test",
    )


class MemoryStore:
    def __init__(self) -> None:
        self.state = AiAssistantState()
        self.saved: list[AiAssistantState] = []

    async def async_load(self) -> AiAssistantState:
        return self.state

    async def async_save(self, state: AiAssistantState) -> None:
        self.state = state
        self.saved.append(state)


class RecordingTransport:
    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls = 0

    async def async_complete(self, settings, api_key, evidence):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


async def evidence() -> dict[str, object]:
    return {
        "version": 1,
        "rooms": [],
        "mismatch_room_ids": [],
        "outdoor_temperature": None,
    }


async def no_delay(_: float) -> None:
    return None


class AiAssistantServiceTest(unittest.TestCase):
    def test_successful_refresh_persists_advisory_and_usage(self) -> None:
        store = MemoryStore()
        transport = RecordingTransport(
            [
                AiProviderCompletion(
                    content=json.dumps(provider_payload()),
                    prompt_tokens=10,
                    completion_tokens=5,
                )
            ]
        )
        service = AiAssistantService(
            settings=settings(),
            api_key="test-key-123",
            store=store,
            evidence_reader=evidence,
            transport=transport,
            now_ms=lambda: NOW,
            sleep=no_delay,
        )

        advisory = asyncio.run(service.async_refresh())

        self.assertIs(AiAdvisoryStatus.READY, advisory.status)
        self.assertEqual(1, store.state.stats.aggregates[0].calls)
        self.assertEqual(1, store.state.stats.aggregates[0].successes)
        self.assertEqual(1, transport.calls)

    def test_401_becomes_provider_error_with_auth_counter(self) -> None:
        store = MemoryStore()
        transport = RecordingTransport([AiProviderHttpError(401)])
        service = AiAssistantService(
            settings=settings(),
            api_key="test-key-123",
            store=store,
            evidence_reader=evidence,
            transport=transport,
            now_ms=lambda: NOW,
            sleep=no_delay,
        )

        advisory = asyncio.run(service.async_refresh())

        self.assertIs(AiAdvisoryStatus.PROVIDER_ERROR, advisory.status)
        self.assertEqual(1, store.state.stats.aggregates[0].auth_errors)
        self.assertEqual(1, transport.calls)

    def test_timeout_retries_once_and_records_each_attempt(self) -> None:
        store = MemoryStore()
        transport = RecordingTransport([AiProviderTimeout(), AiProviderTimeout()])
        service = AiAssistantService(
            settings=settings(),
            api_key="test-key-123",
            store=store,
            evidence_reader=evidence,
            transport=transport,
            now_ms=lambda: NOW,
            sleep=no_delay,
        )

        advisory = asyncio.run(service.async_refresh())

        self.assertIs(AiAdvisoryStatus.PROVIDER_TIMEOUT, advisory.status)
        self.assertEqual(2, store.state.stats.aggregates[0].timeout_errors)
        self.assertEqual(2, transport.calls)

    def test_malformed_provider_json_becomes_invalid_output(self) -> None:
        store = MemoryStore()
        transport = RecordingTransport(
            [AiProviderCompletion(content="not-json", prompt_tokens=0, completion_tokens=0)]
        )
        service = AiAssistantService(
            settings=settings(),
            api_key="test-key-123",
            store=store,
            evidence_reader=evidence,
            transport=transport,
            now_ms=lambda: NOW,
            sleep=no_delay,
        )

        advisory = asyncio.run(service.async_refresh())

        self.assertIs(AiAdvisoryStatus.PROVIDER_OUTPUT_INVALID, advisory.status)
        self.assertEqual(1, store.state.stats.aggregates[0].invalid_errors)

    def test_disabled_or_unconfigured_service_never_calls_provider(self) -> None:
        for configured_settings, api_key, status in (
            (settings(enabled=False), "test-key-123", AiAdvisoryStatus.DISABLED),
            (None, None, AiAdvisoryStatus.UNCONFIGURED),
        ):
            with self.subTest(status=status):
                transport = RecordingTransport([])
                service = AiAssistantService(
                    settings=configured_settings,
                    api_key=api_key,
                    store=MemoryStore(),
                    evidence_reader=evidence,
                    transport=transport,
                    now_ms=lambda: NOW,
                    sleep=no_delay,
                )

                advisory = asyncio.run(service.async_refresh())

                self.assertIs(status, advisory.status)
                self.assertEqual(0, transport.calls)
