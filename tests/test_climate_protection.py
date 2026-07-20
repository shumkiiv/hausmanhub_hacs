"""Tests for restart-safe climate transition and delay memory."""

from __future__ import annotations

from dataclasses import asdict, replace
import json
import unittest

from custom_components.hausman_hub.application.climate_observations import (
    build_climate_observation_snapshot,
)
from custom_components.hausman_hub.application.climate_protection import (
    climate_protection_from_payload,
    climate_protection_to_payload,
    reconcile_climate_protection_memory,
    update_climate_protection,
)
from custom_components.hausman_hub.domain.climate import ClimateRegistry
from custom_components.hausman_hub.domain.climate_observation import (
    ClimateDeviceAvailability,
)
from custom_components.hausman_hub.domain.climate_protection import (
    ClimateDeviceProtectionState,
    ClimateProtectionMemory,
    ClimateProtectionPhase,
    ClimateProtectionViolation,
    empty_climate_protection_memory,
)
from tests.test_contours import setup, source_snapshot


NOW = 1_800_000_000_000


def _inputs():
    registry, _ = setup()
    observation = build_climate_observation_snapshot(
        registry,
        source_snapshot(),
        observed_at=NOW,
    )
    return registry, observation


class ClimateProtectionTest(unittest.TestCase):
    def test_first_observation_seeds_a_conservative_start_time(self) -> None:
        registry, observation = _inputs()
        memory = empty_climate_protection_memory(updated_at=NOW - 1_000)

        result = update_climate_protection(
            memory,
            registry,
            observation,
            restart_rearm_after=None,
        )

        state = result.memory.device("living_air_conditioner")
        device = result.observation.device("living_air_conditioner")
        self.assertIs(state.phase, ClimateProtectionPhase.ACTIVE)  # type: ignore[union-attr]
        self.assertEqual(NOW, state.last_started_at)  # type: ignore[union-attr]
        self.assertEqual(NOW, device.last_started_at)  # type: ignore[union-attr]
        self.assertEqual(0, device.confirmed_short_cycle_count)  # type: ignore[union-attr]
        self.assertTrue(result.changed)
        self.assertTrue(result.rearm_complete)
        self.assertFalse(result.memory.commands_enabled)

    def test_same_phase_retains_transition_time_during_normal_work(self) -> None:
        registry, observation = _inputs()
        first = update_climate_protection(
            empty_climate_protection_memory(updated_at=NOW - 1_000),
            registry,
            observation,
            restart_rearm_after=None,
        )
        later = replace(observation, observed_at=NOW + 60_000)

        second = update_climate_protection(
            first.memory,
            registry,
            later,
            restart_rearm_after=None,
        )

        state = second.memory.device("living_air_conditioner")
        self.assertEqual(NOW, state.last_started_at)  # type: ignore[union-attr]
        self.assertEqual(NOW, state.observed_at)  # type: ignore[union-attr]
        self.assertFalse(second.changed)

    def test_restart_rearms_once_then_keeps_counting_down(self) -> None:
        registry, observation = _inputs()
        stored = ClimateProtectionMemory(
            updated_at=NOW - 60_000,
            devices=(
                ClimateDeviceProtectionState(
                    device_id="living_air_conditioner",
                    room_id="living",
                    phase=ClimateProtectionPhase.ACTIVE,
                    observed_at=NOW - 60_000,
                    last_started_at=NOW - 5 * 60_000,
                    last_stopped_at=None,
                    confirmed_short_cycle_count=2,
                ),
            ),
        )
        cutoff = NOW - 1_000

        rearmed = update_climate_protection(
            stored,
            registry,
            observation,
            restart_rearm_after=cutoff,
        )
        later = replace(observation, observed_at=NOW + 60_000)
        continued = update_climate_protection(
            rearmed.memory,
            registry,
            later,
            restart_rearm_after=cutoff,
        )

        self.assertEqual(
            NOW,
            rearmed.memory.device("living_air_conditioner").last_started_at,  # type: ignore[union-attr]
        )
        self.assertEqual(
            NOW,
            continued.memory.device("living_air_conditioner").last_started_at,  # type: ignore[union-attr]
        )
        self.assertEqual(
            2,
            continued.memory.device(  # type: ignore[union-attr]
                "living_air_conditioner"
            ).confirmed_short_cycle_count,
        )
        self.assertTrue(continued.rearm_complete)

    def test_unavailable_device_keeps_memory_without_inventing_observation(self) -> None:
        registry, observation = _inputs()
        stored = ClimateProtectionMemory(
            updated_at=NOW - 60_000,
            devices=(
                ClimateDeviceProtectionState(
                    device_id="living_air_conditioner",
                    room_id="living",
                    phase=ClimateProtectionPhase.INACTIVE,
                    observed_at=NOW - 60_000,
                    last_started_at=None,
                    last_stopped_at=NOW - 60_000,
                    confirmed_short_cycle_count=0,
                ),
            ),
        )
        unavailable = replace(
            observation,
            devices=tuple(
                replace(
                    device,
                    availability=ClimateDeviceAvailability.UNAVAILABLE,
                    activity=device.activity.UNKNOWN,
                )
                for device in observation.devices
            ),
        )

        result = update_climate_protection(
            stored,
            registry,
            unavailable,
            restart_rearm_after=NOW - 1_000,
        )

        self.assertEqual(stored, result.memory)
        self.assertIsNone(
            result.observation.device("living_air_conditioner").last_stopped_at  # type: ignore[union-attr]
        )
        self.assertFalse(result.rearm_complete)

    def test_registry_reconciliation_drops_removed_and_future_memory(self) -> None:
        registry, observation = _inputs()
        current = update_climate_protection(
            empty_climate_protection_memory(updated_at=NOW - 1_000),
            registry,
            observation,
            restart_rearm_after=None,
        ).memory

        removed, changed = reconcile_climate_protection_memory(
            current,
            ClimateRegistry(),
            now_ms=NOW + 1,
        )
        future, reset = reconcile_climate_protection_memory(
            replace(current, updated_at=NOW + 2),
            registry,
            now_ms=NOW + 1,
        )

        self.assertTrue(changed)
        self.assertEqual((), removed.devices)
        self.assertTrue(reset)
        self.assertEqual(NOW + 1, future.updated_at)
        self.assertEqual((), future.devices)

    def test_storage_round_trip_is_exact_private_free_and_strict(self) -> None:
        registry, observation = _inputs()
        memory = update_climate_protection(
            empty_climate_protection_memory(updated_at=NOW - 1_000),
            registry,
            observation,
            restart_rearm_after=None,
        ).memory
        payload = climate_protection_to_payload(memory)

        self.assertEqual(memory, climate_protection_from_payload(payload))
        serialized = json.dumps(asdict(memory), ensure_ascii=False)
        for hidden in (
            "source_id",
            "entity_id",
            "service",
            "endpoint",
            "command",
            "backend_payload",
        ):
            self.assertNotIn(hidden, serialized)
        with self.assertRaises(ClimateProtectionViolation):
            climate_protection_from_payload({**payload, "extra": True})
        with self.assertRaises(ClimateProtectionViolation):
            climate_protection_from_payload({**payload, "version": True})
        with self.assertRaises(ClimateProtectionViolation):
            replace(memory, version=True)
        with self.assertRaises(ClimateProtectionViolation):
            climate_protection_from_payload(
                {**payload, "devices": tuple(payload["devices"])}  # type: ignore[arg-type]
            )
        with self.assertRaises(ClimateProtectionViolation):
            replace(memory, devices=list(memory.devices))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
