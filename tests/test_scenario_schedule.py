"""Tests for the pure scenario scheduling math."""

from __future__ import annotations

import unittest
import importlib
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.hausman_hub.application.scenario_schedule import (
    ScheduledRun,
    _next_clock_run,
    compute_upcoming_runs,
    prune_skip_keys,
    skip_key_for,
)
from custom_components.hausman_hub.domain.scenarios import (
    Scenario,
    ScenarioDefinition,
)

TZ = timezone(timedelta(hours=6))  # Asia/Omsk, UTC+6
NOW = datetime(2026, 8, 9, 10, 0, 0, tzinfo=TZ)


def _scenario(
    scenario_id: str,
    triggers: list[dict[str, object]],
    *,
    enabled: bool = True,
) -> Scenario:
    definition = ScenarioDefinition.from_payload(
        {
            "version": 1,
            "executionMode": "single",
            "triggers": triggers,
            "conditions": [],
            "actions": [
                {
                    "id": "a1",
                    "type": "device_action",
                    "targetId": "device_abc",
                    "actionId": "turn_on",
                    "command": {
                        "domain": "light",
                        "service": "turn_on",
                        "entity_id": "light.living_room",
                    },
                }
            ],
        }
    )
    return Scenario.from_definition(
        scenario_id, f"Scenario {scenario_id}", definition, enabled=enabled
    )


class ComputeUpcomingRunsTest(unittest.TestCase):
    def test_time_trigger_later_today(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "time", "value": "12:30"}])
        runs = compute_upcoming_runs([scenario], NOW, None, None)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 9, 12, 30, tzinfo=TZ))
        self.assertEqual(runs[0].trigger_type, "time")
        self.assertEqual(runs[0].scenario_title, "Scenario s1")

    def test_time_trigger_past_rolls_to_tomorrow(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "time", "value": "08:00"}])
        runs = compute_upcoming_runs([scenario], NOW, None, None)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 10, 8, 0, tzinfo=TZ))

    def test_time_trigger_exact_now_rolls_to_tomorrow(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "time", "value": "10:00"}])
        runs = compute_upcoming_runs([scenario], NOW, None, None)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 10, 10, 0, tzinfo=TZ))

    def test_time_trigger_invalid_value_returns_none(self) -> None:
        for bad in ("8:00", "abc", 800, None):
            self.assertIsNone(_next_clock_run(bad, NOW))

    def test_sunrise_with_offset(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "sunrise", "value": 30}])
        next_sunrise = datetime(2026, 8, 10, 5, 0, tzinfo=TZ)
        runs = compute_upcoming_runs([scenario], NOW, next_sunrise, None)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 10, 5, 30, tzinfo=TZ))

    def test_sunrise_offset_numeric_string(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "sunrise", "value": "45"}])
        next_sunrise = datetime(2026, 8, 10, 5, 0, tzinfo=TZ)
        runs = compute_upcoming_runs([scenario], NOW, next_sunrise, None)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 10, 5, 45, tzinfo=TZ))

    def test_sunset_negative_offset(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "sunset", "value": -15}])
        next_sunset = datetime(2026, 8, 9, 20, 0, tzinfo=TZ)
        runs = compute_upcoming_runs([scenario], NOW, None, next_sunset)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 9, 19, 45, tzinfo=TZ))

    def test_sunset_without_offset_means_zero(self) -> None:
        # Триггер заката без смещения: системный сценарий «Сумерки» от
        # 2026-08-20 именно такой; раньше адаптеры падали на None.
        scenario = _scenario("s1", [{"id": "t1", "type": "sunset"}])
        next_sunset = datetime(2026, 8, 9, 20, 0, tzinfo=TZ)
        runs = compute_upcoming_runs([scenario], NOW, None, next_sunset)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 9, 20, 0, tzinfo=TZ))

    def test_sun_base_converted_to_local_tz(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "sunset", "value": 0}])
        next_sunset_utc = datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)
        runs = compute_upcoming_runs([scenario], NOW, None, next_sunset_utc)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].run_at.tzinfo, TZ)
        self.assertEqual(runs[0].run_at, datetime(2026, 8, 9, 20, 0, tzinfo=TZ))

    def test_sun_without_astronomy_skipped(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "sunset", "value": 0}])
        self.assertEqual(compute_upcoming_runs([scenario], NOW, None, None), [])

    def test_sun_run_already_passed_skipped(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "sunrise", "value": 0}])
        next_sunrise = datetime(2026, 8, 9, 6, 0, tzinfo=TZ)  # before NOW
        self.assertEqual(
            compute_upcoming_runs([scenario], NOW, next_sunrise, None), []
        )

    def test_disabled_scenario_skipped(self) -> None:
        scenario = _scenario(
            "s1", [{"id": "t1", "type": "time", "value": "12:00"}], enabled=False
        )
        self.assertEqual(compute_upcoming_runs([scenario], NOW, None, None), [])

    def test_manual_and_device_triggers_ignored(self) -> None:
        scenario = _scenario(
            "s1",
            [
                {"id": "t1", "type": "manual"},
                {
                    "id": "t2",
                    "type": "device_state",
                    "targetId": "device_abc",
                    "property": "state",
                    "comparison": "equals",
                    "value": "on",
                },
            ],
        )
        self.assertEqual(compute_upcoming_runs([scenario], NOW, None, None), [])

    def test_skipped_key_excluded(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "time", "value": "12:00"}])
        skipped = {skip_key_for("s1", "t1", "2026-08-09")}
        self.assertEqual(
            compute_upcoming_runs([scenario], NOW, None, None, skipped), []
        )

    def test_runs_sorted_by_run_at(self) -> None:
        early = _scenario("s1", [{"id": "t1", "type": "time", "value": "11:00"}])
        late = _scenario("s2", [{"id": "t2", "type": "time", "value": "18:00"}])
        runs = compute_upcoming_runs([late, early], NOW, None, None)
        self.assertEqual([run.scenario_id for run in runs], ["s1", "s2"])

    def test_horizon_excludes_far_runs(self) -> None:
        scenario = _scenario("s1", [{"id": "t1", "type": "time", "value": "12:00"}])
        runs = compute_upcoming_runs(
            [scenario], NOW, None, None, horizon=timedelta(hours=1)
        )
        self.assertEqual(runs, [])


class SkipKeyTest(unittest.TestCase):
    def test_skip_key_format(self) -> None:
        self.assertEqual(
            skip_key_for("s1", "t1", "2026-08-09"), "s1|t1|2026-08-09"
        )

    def test_scheduled_run_skip_key(self) -> None:
        run = ScheduledRun(
            scenario_id="s1",
            scenario_title="Title",
            trigger_id="t1",
            trigger_type="time",
            run_at=datetime(2026, 8, 9, 12, 0, tzinfo=TZ),
        )
        self.assertEqual(run.skip_key, "s1|t1|2026-08-09")

    def test_prune_drops_past_days(self) -> None:
        keys = {"s1|t1|2026-08-08", "s1|t1|2026-08-09", "s1|t1|2026-08-10"}
        self.assertEqual(
            prune_skip_keys(keys, "2026-08-09"),
            {"s1|t1|2026-08-09", "s1|t1|2026-08-10"},
        )

    def test_prune_empty(self) -> None:
        self.assertEqual(prune_skip_keys([], "2026-08-09"), set())


@pytest.mark.asyncio
async def test_only_zero_offset_real_sunrise_is_trusted(monkeypatch) -> None:
    callbacks: list[tuple[str, object, object]] = []

    def track_time(_hass, callback, **_kwargs):
        callbacks.append(("time", None, callback))
        return lambda: None

    def track_sunrise(_hass, callback, *, offset):
        callbacks.append(("sunrise", offset, callback))
        return lambda: None

    def track_sunset(_hass, callback, *, offset):
        callbacks.append(("sunset", offset, callback))
        return lambda: None

    homeassistant = ModuleType("homeassistant")
    helpers = ModuleType("homeassistant.helpers")
    ha_event = ModuleType("homeassistant.helpers.event")
    ha_event.async_track_time_interval = lambda *_args, **_kwargs: lambda: None
    ha_event.async_track_time_change = track_time
    ha_event.async_track_sunrise = track_sunrise
    ha_event.async_track_sunset = track_sunset
    util = ModuleType("homeassistant.util")
    dt = ModuleType("homeassistant.util.dt")
    dt.now = lambda: datetime.now(timezone.utc)
    util.dt = dt
    monkeypatch.setitem(sys.modules, "homeassistant", homeassistant)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.event", ha_event)
    monkeypatch.setitem(sys.modules, "homeassistant.util", util)
    monkeypatch.setitem(sys.modules, "homeassistant.util.dt", dt)
    schedule_adapter = importlib.import_module(
        "custom_components.hausman_hub.scenario_schedule"
    )
    monkeypatch.setattr(
        schedule_adapter,
        "async_track_time_interval",
        lambda *_args, **_kwargs: (lambda: None),
    )

    service = SimpleNamespace(
        scheduled_trigger_items=lambda: (
            ("system-curtains-privacy-controller", "sunrise", "time", "06:00"),
            ("system-curtains-privacy-controller", "sunrise", "sunset", 0),
            ("system-curtains-privacy-controller", "sunrise", "sunrise", 15),
            ("system-curtains-privacy-controller", "sunrise", "sunrise", 0),
        ),
        async_consume_skip=AsyncMock(return_value=False),
        async_run_scenario=AsyncMock(return_value={"status": "completed"}),
    )
    protection = SimpleNamespace(
        async_run_trusted_sunrise=AsyncMock(
            return_value={"status": "completed"}
        )
    )
    unloads = []
    entry = SimpleNamespace(async_on_unload=unloads.append)
    await schedule_adapter.async_start_scenario_schedule(
        SimpleNamespace(), entry, service, curtain_protection=protection
    )

    now = datetime(2026, 9, 7, 5, 0, tzinfo=timezone.utc)
    for _kind, _offset, callback in callbacks:
        await callback(now)

    assert protection.async_run_trusted_sunrise.await_count == 1
    protection.async_run_trusted_sunrise.assert_awaited_once_with(
        int(now.timestamp() * 1000), service.async_run_scenario
    )
    assert service.async_run_scenario.await_count == 3


if __name__ == "__main__":
    unittest.main()
