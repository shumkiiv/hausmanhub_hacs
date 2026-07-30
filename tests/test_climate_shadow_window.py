"""Tests for bounded, persistent, command-free climate shadow evidence."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.climate_comparison import (
    climate_reference_comparison,
)
from custom_components.hausman_hub.application.climate_shadow_window import (
    ClimateShadowWindowService,
    append_climate_shadow_sample,
    climate_shadow_sample_from_comparison,
    climate_shadow_state_from_payload,
    climate_shadow_state_to_payload,
    climate_shadow_window_to_payload,
)
from custom_components.hausman_hub.domain.climate_shadow_window import (
    ClimateShadowVerdict,
    ClimateShadowWindowPolicy,
    ClimateShadowWindowState,
    ClimateShadowWindowViolation,
)


ROOT = Path(__file__).resolve().parents[1]
BASE = 1_785_330_000_000
POLICY = ClimateShadowWindowPolicy(
    sample_interval_seconds=300,
    retention_seconds=3600,
    minimum_sample_count=3,
    minimum_span_seconds=600,
    freshness_seconds=600,
    minimum_alignment_ratio=1.0,
)


class MemoryStore:
    def __init__(self) -> None:
        self.state: ClimateShadowWindowState | None = None
        self.save_count = 0

    async def async_load(self) -> ClimateShadowWindowState | None:
        return self.state

    async def async_save(self, state: ClimateShadowWindowState) -> None:
        self.state = state
        self.save_count += 1


class UnreadableStore(MemoryStore):
    async def async_load(self) -> ClimateShadowWindowState | None:
        raise RuntimeError("synthetic unreadable storage")


def sample(case_id: str, observed_at: int):
    comparison = replace(
        climate_reference_comparison(case_id),
        observed_at=observed_at,
    )
    return climate_shadow_sample_from_comparison(comparison)


class ClimateShadowWindowTest(unittest.TestCase):
    def test_aligned_window_becomes_ready_only_after_count_span_and_freshness(self) -> None:
        state = ClimateShadowWindowState()
        for offset in (0, 300_000, 600_000):
            state = append_climate_shadow_sample(
                state,
                sample("stopped_ac_starts_at_default_gap", BASE + offset),
                collected_at=BASE + offset,
                policy=POLICY,
            )

        payload = climate_shadow_window_to_payload(
            state,
            generated_at=BASE + 720_000,
            policy=POLICY,
            collection_active=True,
        )
        room = payload["rooms"][0]

        self.assertEqual("ready", room["verdict"])
        self.assertEqual([], room["reasons"])
        self.assertEqual(1.0, room["alignment_ratio"])
        self.assertFalse(payload["commands_enabled"])
        self.assertFalse(payload["physical_commands_sent"])
        schema = json.loads(
            (
                ROOT
                / "custom_components/hausman_hub/contracts/v1/climate-shadow-window.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(payload)

    def test_one_confirmed_divergence_blocks_room_even_before_window_is_complete(self) -> None:
        state = ClimateShadowWindowState(
            samples=(
                sample("stopped_ac_starts_at_default_gap", BASE),
                sample("cooldown_delays_repeated_intent", BASE + 300_000),
            )
        )

        payload = climate_shadow_window_to_payload(
            state,
            generated_at=BASE + 360_000,
            policy=POLICY,
            collection_active=True,
        )
        room = payload["rooms"][0]

        self.assertEqual(ClimateShadowVerdict.DIVERGED.value, room["verdict"])
        self.assertIn("divergence_observed", room["reasons"])
        self.assertIn("alignment_below_threshold", room["reasons"])

    def test_not_comparable_and_stale_evidence_stays_insufficient(self) -> None:
        state = ClimateShadowWindowState(
            samples=(sample("manual_mode_observes", BASE),)
        )

        payload = climate_shadow_window_to_payload(
            state,
            generated_at=BASE + 1_000_000,
            policy=POLICY,
            collection_active=True,
        )
        room = payload["rooms"][0]

        self.assertEqual("insufficient_data", room["verdict"])
        self.assertEqual(
            [
                "insufficient_observations",
                "insufficient_timespan",
                "latest_observation_stale",
                "not_comparable_observations",
            ],
            room["reasons"],
        )

    def test_retention_prunes_old_samples_and_same_time_replaces_without_growth(self) -> None:
        old = sample("stopped_ac_starts_at_default_gap", BASE)
        current = sample("manual_mode_observes", BASE + 3_600_000)
        state = ClimateShadowWindowState(samples=(old,))

        state = append_climate_shadow_sample(
            state,
            current,
            collected_at=BASE + 3_600_001,
            policy=POLICY,
        )
        state = append_climate_shadow_sample(
            state,
            current,
            collected_at=BASE + 3_600_001,
            policy=POLICY,
        )

        self.assertEqual((current,), state.samples)

    def test_service_persists_and_restores_the_same_redacted_state(self) -> None:
        store = MemoryStore()
        service = ClimateShadowWindowService(store, policy=POLICY)

        async def exercise() -> dict[str, object]:
            await service.async_load()
            comparison = replace(
                climate_reference_comparison("stopped_ac_starts_at_default_gap"),
                observed_at=BASE,
            )
            self.assertTrue(
                await service.async_record(comparison, collected_at=BASE)
            )
            self.assertFalse(
                await service.async_record(comparison, collected_at=BASE)
            )
            restarted = ClimateShadowWindowService(store, policy=POLICY)
            await restarted.async_load()
            return await restarted.async_snapshot(generated_at=BASE + 1)

        payload = asyncio.run(exercise())

        self.assertEqual(1, store.save_count)
        self.assertEqual(1, payload["summary"]["sample_count"])
        self.assertFalse(hasattr(service, "async_execute"))
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("entity_id", serialized)
        self.assertNotIn("service", serialized)

    def test_empty_recovery_allows_a_valid_sample_to_replace_unreadable_state(self) -> None:
        store = UnreadableStore()
        service = ClimateShadowWindowService(store, policy=POLICY)

        async def exercise() -> dict[str, object]:
            with self.assertRaises(RuntimeError):
                await service.async_load()
            await service.async_initialize_empty()
            comparison = replace(
                climate_reference_comparison("stopped_ac_starts_at_default_gap"),
                observed_at=BASE,
            )
            await service.async_record(comparison, collected_at=BASE)
            return await service.async_snapshot(generated_at=BASE + 1)

        payload = asyncio.run(exercise())

        self.assertTrue(payload["window"]["collection_active"])
        self.assertEqual(1, payload["summary"]["sample_count"])
        self.assertEqual(1, store.save_count)

    def test_storage_round_trip_is_exact_and_rejects_extra_private_fields(self) -> None:
        state = ClimateShadowWindowState(
            samples=(sample("stopped_ac_starts_at_default_gap", BASE),)
        )
        payload = climate_shadow_state_to_payload(state)

        self.assertEqual(state, climate_shadow_state_from_payload(payload))
        payload["entity_id"] = "climate.private"
        with self.assertRaises(ClimateShadowWindowViolation):
            climate_shadow_state_from_payload(payload)


if __name__ == "__main__":
    unittest.main()
