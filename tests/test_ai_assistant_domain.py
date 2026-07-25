from __future__ import annotations

import unittest

from custom_components.hausman_hub.domain.ai_assistant import (
    AiAdvisoryStatus,
    AiAssistantSettings,
    AiAssistantViolation,
    AiProviderPreset,
    AiUsageCall,
    AiUsageStats,
    ai_advisory_from_payload,
)


def provider_payload() -> dict[str, object]:
    return {
        "version": 1,
        "source": "provider",
        "generatedAt": 1_800_000_000_000,
        "summary": {"code": "advisory_available"},
        "recommendations": [
            {
                "code": "review_temperature_gap",
                "priority": "warning",
                "evidence": ["temperature_above_comfort"],
            }
        ],
        "riskFlags": [
            {
                "code": "temperature_outside_comfort_band",
                "severity": "warning",
                "evidence": ["temperature_above_comfort"],
            }
        ],
    }


class AiAssistantSettingsTest(unittest.TestCase):
    def test_settings_allow_http_only_for_private_or_loopback_hosts(self) -> None:
        private = AiAssistantSettings(
            enabled=True,
            preset=AiProviderPreset.CUSTOM,
            base_url="http://192.168.1.50/v1",
            model="local-climate",
        )
        loopback = AiAssistantSettings(
            enabled=True,
            preset=AiProviderPreset.CUSTOM,
            base_url="http://localhost:8080/v1",
            model="local-climate",
        )

        self.assertEqual("http://192.168.1.50/v1", private.base_url)
        self.assertEqual("http://localhost:8080/v1", loopback.base_url)

    def test_settings_reject_ssrf_and_public_http_endpoints(self) -> None:
        for base_url in (
            "http://169.254.169.254/latest/meta-data",
            "http://api.example.com/v1",
            "file:///etc/passwd",
            "ftp://provider.example/v1",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(AiAssistantViolation) as raised:
                    AiAssistantSettings(
                        enabled=True,
                        preset=AiProviderPreset.CUSTOM,
                        base_url=base_url,
                        model="climate-model",
                    )
                self.assertEqual("invalid_base_url", raised.exception.code)


class AiAdvisoryPayloadTest(unittest.TestCase):
    def test_parser_accepts_exact_bounded_provider_advisory(self) -> None:
        advisory = ai_advisory_from_payload(provider_payload())

        self.assertIs(AiAdvisoryStatus.READY, advisory.status)
        self.assertEqual("advisory_available", advisory.summary)
        self.assertEqual("review_temperature_gap", advisory.recommendations[0].code)
        self.assertEqual(
            "temperature_outside_comfort_band",
            advisory.risk_flags[0].code,
        )

    def test_parser_rejects_unknown_or_command_shaped_fields(self) -> None:
        payload = provider_payload()
        payload["command"] = {"service": "climate.set_temperature"}

        with self.assertRaises(AiAssistantViolation) as raised:
            ai_advisory_from_payload(payload)

        self.assertEqual("invalid_advisory_payload", raised.exception.code)

    def test_parser_rejects_wrong_nested_value_types(self) -> None:
        payload = provider_payload()
        recommendations = payload["recommendations"]
        assert isinstance(recommendations, list)
        recommendations[0]["priority"] = ["warning"]

        with self.assertRaises(AiAssistantViolation) as raised:
            ai_advisory_from_payload(payload)

        self.assertEqual("invalid_advisory_payload", raised.exception.code)


class AiUsageStatsTest(unittest.TestCase):
    def test_usage_stats_reject_mutable_collections(self) -> None:
        with self.assertRaises(AiAssistantViolation) as raised:
            AiUsageStats(aggregates=[], recent_calls=())

        self.assertEqual("invalid_usage_stats", raised.exception.code)

    def test_usage_stats_bound_recent_calls_to_150_entries(self) -> None:
        stats = AiUsageStats()
        for index in range(151):
            stats = stats.with_call(
                AiUsageCall(
                    ts=1_800_000_000_000 + index,
                    preset=AiProviderPreset.DEEPSEEK,
                    model="deepseek-chat",
                    status=AiAdvisoryStatus.READY,
                    summary_code="advisory_available",
                    prompt_tokens=10,
                    completion_tokens=5,
                    latency_ms=20,
                    error_class=None,
                )
            )

        self.assertEqual(151, stats.aggregates[0].calls)
        self.assertEqual(150, len(stats.recent_calls))
        self.assertEqual(1_800_000_000_001, stats.recent_calls[0].ts)
        self.assertEqual(1_800_000_000_150, stats.recent_calls[-1].ts)
