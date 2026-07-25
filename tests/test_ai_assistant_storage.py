from __future__ import annotations

import unittest

from custom_components.hausman_hub.application.ai_assistant_storage import (
    ai_assistant_state_from_payload,
    ai_assistant_state_to_payload,
)
from custom_components.hausman_hub.domain.ai_assistant import (
    AiAssistantViolation,
    AiUsageCall,
    AiUsageStats,
    AiProviderPreset,
    AiAdvisoryStatus,
    ai_advisory_from_payload,
)
from custom_components.hausman_hub.domain.ai_assistant_state import AiAssistantState


def state() -> AiAssistantState:
    advisory = ai_advisory_from_payload(
        {
            "version": 1,
            "source": "provider",
            "generatedAt": 1_800_000_000_000,
            "summary": {"code": "advisory_available"},
            "recommendations": [],
            "riskFlags": [],
        }
    )
    stats = AiUsageStats().with_call(
        AiUsageCall(
            ts=1_800_000_000_000,
            preset=AiProviderPreset.OPENAI,
            model="gpt-4.1-mini",
            status=AiAdvisoryStatus.READY,
            summary_code="advisory_available",
            prompt_tokens=12,
            completion_tokens=8,
            latency_ms=42,
            error_class=None,
        )
    )
    return AiAssistantState(last_advisory=advisory, stats=stats)


class AiAssistantStorageCodecTest(unittest.TestCase):
    def test_storage_round_trip_keeps_only_advisory_and_usage_facts(self) -> None:
        payload = ai_assistant_state_to_payload(state())

        restored = ai_assistant_state_from_payload(payload)

        self.assertEqual(state(), restored)
        self.assertNotIn("api_key", str(payload))
        self.assertNotIn("prompt_text", str(payload))
        self.assertNotIn("response_text", str(payload))

    def test_storage_rejects_unknown_version(self) -> None:
        payload = ai_assistant_state_to_payload(state())
        payload["version"] = 2

        with self.assertRaises(AiAssistantViolation) as raised:
            ai_assistant_state_from_payload(payload)

        self.assertEqual("invalid_ai_assistant_state", raised.exception.code)
