"""Tests for the one-room internal climate trial gate and executor."""

from __future__ import annotations

import unittest

from custom_components.hausman_hub.climate_ha_executor import (
    ClimateHaExecutionError,
    HomeAssistantClimateCallExecutor,
)
from custom_components.hausman_hub.domain.climate_ha_calls import (
    ClimateHaHvacMode,
    ClimateHaService,
    ClimateHaServiceCall,
)
from custom_components.hausman_hub.domain.climate_observation import ClimateFanMode
from custom_components.hausman_hub.domain.climate_trial import (
    ClimateTrialDecision,
    ClimateTrialReceipt,
    ClimateTrialReason,
    ClimateTrialStatus,
    ClimateTrialViolation,
)


NOW = 1_800_000_000_000


def _calls() -> tuple[ClimateHaServiceCall, ...]:
    return (
        ClimateHaServiceCall(
            service=ClimateHaService.CLIMATE_SET_HVAC_MODE,
            entity_id="climate.living_ac",
            hvac_mode=ClimateHaHvacMode.COOL,
        ),
        ClimateHaServiceCall(
            service=ClimateHaService.CLIMATE_SET_TEMPERATURE,
            entity_id="climate.living_ac",
            temperature=26.0,
        ),
        ClimateHaServiceCall(
            service=ClimateHaService.CLIMATE_SET_FAN_MODE,
            entity_id="climate.living_ac",
            fan_mode=ClimateFanMode.LOW,
        ),
    )


class _FakeServices:
    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.fail_at = fail_at

    async def async_call(self, domain, service, data, blocking=False):
        if self.fail_at is not None and len(self.calls) >= self.fail_at:
            raise RuntimeError("synthetic service failure")
        self.calls.append((domain, service, data))


class _FakeHass:
    def __init__(self, fail_at: int | None = None) -> None:
        self.services = _FakeServices(fail_at)


class ClimateHaExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_executor_maps_only_strict_service_data(self) -> None:
        hass = _FakeHass()
        executor = HomeAssistantClimateCallExecutor(hass)  # type: ignore[arg-type]

        completed = await executor.async_execute(_calls())

        self.assertEqual(3, completed)
        self.assertEqual(
            (
                "climate",
                "set_hvac_mode",
                {"entity_id": "climate.living_ac", "hvac_mode": "cool"},
            ),
            hass.services.calls[0],
        )
        self.assertEqual(
            (
                "climate",
                "set_temperature",
                {"entity_id": "climate.living_ac", "temperature": 26.0},
            ),
            hass.services.calls[1],
        )
        self.assertEqual(
            (
                "climate",
                "set_fan_mode",
                {"entity_id": "climate.living_ac", "fan_mode": "low"},
            ),
            hass.services.calls[2],
        )

    async def test_executor_stops_at_the_first_failure(self) -> None:
        hass = _FakeHass(fail_at=1)
        executor = HomeAssistantClimateCallExecutor(hass)  # type: ignore[arg-type]

        with self.assertRaises(ClimateHaExecutionError) as caught:
            await executor.async_execute(_calls())

        self.assertEqual(1, caught.exception.completed)
        self.assertEqual(1, len(hass.services.calls))

    async def test_executor_runs_power_calls_with_entity_only(self) -> None:
        hass = _FakeHass()
        executor = HomeAssistantClimateCallExecutor(hass)  # type: ignore[arg-type]
        calls = (
            ClimateHaServiceCall(
                service=ClimateHaService.HUMIDIFIER_TURN_ON,
                entity_id="humidifier.kids",
            ),
        )

        completed = await executor.async_execute(calls)

        self.assertEqual(1, completed)
        self.assertEqual(
            ("humidifier", "turn_on", {"entity_id": "humidifier.kids"}),
            hass.services.calls[0],
        )


class ClimateTrialModelTest(unittest.TestCase):
    def test_decision_rejects_contradictory_shapes(self) -> None:
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialDecision(
                room_id="living",
                observed_at=NOW,
                permitted=True,
                reasons=(ClimateTrialReason.UP_TO_DATE,),
                calls=_calls(),
            )
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialDecision(
                room_id="living",
                observed_at=NOW,
                permitted=True,
                reasons=(),
                calls=(),
            )
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialDecision(
                room_id="living",
                observed_at=NOW,
                permitted=False,
                reasons=(),
                calls=(),
            )
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialDecision(
                room_id="living",
                observed_at=NOW,
                permitted=False,
                reasons=(ClimateTrialReason.UP_TO_DATE,),
                calls=_calls(),
            )

    def test_receipt_rejects_contradictory_shapes(self) -> None:
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialReceipt(
                room_id="living",
                observed_at=NOW,
                status=ClimateTrialStatus.APPLIED,
                reasons=(),
                call_count=2,
                executed_count=1,
            )
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialReceipt(
                room_id="living",
                observed_at=NOW,
                status=ClimateTrialStatus.DENIED,
                reasons=(ClimateTrialReason.SERVICE_ERROR,),
                call_count=0,
                executed_count=0,
            )
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialReceipt(
                room_id="living",
                observed_at=NOW,
                status=ClimateTrialStatus.FAILED,
                reasons=(ClimateTrialReason.UP_TO_DATE,),
                call_count=2,
                executed_count=0,
            )
        with self.assertRaises(ClimateTrialViolation):
            ClimateTrialReceipt(
                room_id="living",
                observed_at=NOW,
                status=ClimateTrialStatus.UP_TO_DATE,
                reasons=(ClimateTrialReason.UP_TO_DATE,),
                call_count=0,
                executed_count=0,
                version=True,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
