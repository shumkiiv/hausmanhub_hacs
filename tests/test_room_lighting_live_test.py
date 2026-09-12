"""Tests for the bounded room lighting live test runner."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import unittest
from datetime import datetime, time, timezone

import pytest
from jsonschema import Draft202012Validator

from custom_components.hausman_hub.application.room_lighting_live_test import (
    LIVE_TEST_DURATION_SECONDS,
    LIVE_TEST_STAGES,
    RoomLightingLiveTestRunner,
    RoomLightingLiveTestViolation,
    build_stages,
)
from custom_components.hausman_hub.domain.room_lighting import (
    SensorKind,
    config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightSnapshot,
    RoomLightingContext,
    SensorSnapshot,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState

_TZ = timezone.utc
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_NOW = int(datetime(2026, 9, 11, 10, 0, tzinfo=_TZ).timestamp() * 1000)
_CONTRACT_SCHEMA = Path(
    os.environ.get(
        "HAUSMANHUB_CONTRACT_DIR",
        "/home/ivsh/projects/HausmanHub/worktrees/"
        "codex-room-lighting-contract-2026-09-11/schemas/v1",
    )
) / "room-lighting-live-test.schema.json"


def _validate_schema(payload: dict[str, object]) -> None:
    if not _CONTRACT_SCHEMA.is_file():
        raise unittest.SkipTest("room lighting contract schema is not available")
    schema = json.loads(_CONTRACT_SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(payload)


async def _no_sleep(_seconds: float) -> None:
    await asyncio.sleep(0)


def _config():
    payload = {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "room_demo_entry",
        "name": "Тамбур",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_demo_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.demo_presence",
                    "autoAdoptOverride": None,
                },
                {
                    "id": "sensor_demo_lux",
                    "name": "Освещённость",
                    "kind": "illuminance",
                    "entityId": "sensor.demo_lux",
                    "autoAdoptOverride": None,
                },
            ],
            "light_targets": [
                {
                    "id": "light_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                }
            ],
            "power_switch": None,
            "wireless_switches": [
                {
                    "id": "sw_demo_wall",
                    "name": "Настенный",
                    "entityId": "sensor.demo_wall_action",
                    "buttons": ["left"],
                    "pressTypes": ["single"],
                }
            ],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_on_presence",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            },
            {
                "id": "sch_always",
                "title": "Зеркало всегда",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "23:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 10,
                    "colorTemperature": None,
                    "fade": True,
                    "mode": "always",
                    "minOnSeconds": 0,
                },
            },
            {
                "id": "sch_night",
                "title": "Ночная подсветка",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "02:00", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": 10,
                    "colorTemperature": None,
                    "fade": True,
                    "mode": "night_light",
                    "minOnSeconds": 600,
                },
            },
            {
                "id": "sch_off",
                "title": "Выключение",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "23:30", "offsetMinutes": 0},
                },
                "targets": {"lightTargets": ["light_main"], "groupIds": [], "roles": []},
                "how": {
                    "brightness": None,
                    "colorTemperature": None,
                    "fade": True,
                    "mode": "off",
                    "minOnSeconds": 0,
                },
            },
        ],
        "switchBindings": [],
        "illumination": {
            "sensor": "sensor.demo_lux",
            "calibration": {"offset": 0, "multiplier": 1},
            "hysteresis": 5,
            "minLux": 0,
            "maxLux": 20000,
            "thresholds": [
                {"lux": 200, "brightness": 20, "colorTemperature": 2700, "modifier": None}
            ],
            "failClosed": True,
        },
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 20,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {"mode": "room_off", "return": {"restore": "by_current_conditions"}},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }
    return config_from_payload(payload)


def _context_factory(now: int = _NOW):
    def factory(_stage):
        return RoomLightingContext(
            now=now,
            timezone=_TZ,
            sunrise=time(7, 0),
            sunset=time(19, 0),
            sensors=(
                SensorSnapshot("sensor_demo_presence", SensorKind.PRESENCE, SensorState.ON, now),
                SensorSnapshot(
                    "sensor_demo_lux",
                    SensorKind.ILLUMINANCE,
                    SensorState.ON,
                    now,
                    lux=300.0,
                    lux_healthy=True,
                ),
            ),
            lights=(LightSnapshot("light_main", SensorState.OFF, now),),
        )

    return factory


class _SpyExecutor:
    def __init__(self) -> None:
        self.calls: list[object] = []

    async def __call__(self, command: object) -> dict[str, object]:
        self.calls.append(command)
        return {"confirmed": True}


def test_build_stages_uses_canonical_stages_and_is_near_30_seconds() -> None:
    stages = build_stages(_config())
    seen = {stage.stage for stage in stages}
    assert seen <= set(LIVE_TEST_STAGES)
    assert {
        "resolve_devices",
        "snapshot_state",
        "resolve_illumination",
        "simulate_presence",
        "apply_schedule",
        "verify_brightness",
        "verify_ownership",
        "simulate_absence",
        "verify_fade",
        "verify_manual_protection",
        "simulate_switch_press",
        "away_room_off",
        "verify_away_return",
        "restore_state",
    } <= seen
    total = sum(stage.duration_seconds for stage in stages)
    assert 20 <= total <= 40
    for stage in stages:
        assert stage.title and stage.comment and stage.action
        assert _CYRILLIC.search(stage.title)
        assert _CYRILLIC.search(stage.comment)
        assert stage.duration_seconds >= 2


async def test_safe_run_never_calls_executor_and_is_schema_valid() -> None:
    spy = _SpyExecutor()
    trace = await RoomLightingLiveTestRunner(sleep=_no_sleep).run(
        _config(),
        mode="safe",
        correlation_id="live-safe-001",
        executor=spy,
        context_factory=_context_factory(),
    )

    assert spy.calls == []
    assert trace.commands_sent == 0
    assert trace.status == "passed"
    assert trace.mode == "safe"
    assert trace.correlation_id == "live-safe-001"
    assert trace.duration_seconds == LIVE_TEST_DURATION_SECONDS
    assert trace.steps
    assert all(step.offset_seconds <= LIVE_TEST_DURATION_SECONDS for step in trace.steps)
    _validate_schema(trace.to_payload())
    _validate_schema(trace.to_request_payload())


async def test_real_run_dispatches_and_is_schema_valid() -> None:
    spy = _SpyExecutor()
    trace = await RoomLightingLiveTestRunner(sleep=_no_sleep).run(
        _config(),
        mode="real",
        correlation_id="live-real-001",
        executor=spy,
        context_factory=_context_factory(),
    )

    assert spy.calls
    assert trace.commands_sent == len(spy.calls)
    assert trace.status == "passed"
    _validate_schema(trace.to_payload())


async def test_real_mode_requires_executor() -> None:
    with pytest.raises(RoomLightingLiveTestViolation):
        await RoomLightingLiveTestRunner(sleep=_no_sleep).run(
            _config(),
            mode="real",
            correlation_id="live-real-002",
        )


async def test_cancel_stops_the_run_and_is_schema_valid() -> None:
    cancel_event = asyncio.Event()
    cancel_event.set()
    trace = await RoomLightingLiveTestRunner(sleep=_no_sleep).run(
        _config(),
        mode="safe",
        correlation_id="live-cancel-001",
        cancel_event=cancel_event,
        context_factory=_context_factory(),
    )

    assert trace.status == "cancelled"
    assert trace.reason == "cancelled_by_user"
    assert trace.commands_sent == 0
    assert trace.steps == ()
    _validate_schema(trace.to_payload())


async def test_runner_sleeps_real_stage_durations_and_reads_live_context() -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    provider_calls = {"count": 0}

    async def provider():
        provider_calls["count"] += 1
        return _context_factory()(None)

    spy = _SpyExecutor()
    trace = await RoomLightingLiveTestRunner(sleep=fake_sleep).run(
        _config(),
        mode="real",
        correlation_id="live-real-honest-1",
        executor=spy,
        context_provider=provider,
    )

    stages = build_stages(_config())
    assert sleeps == [stage.duration_seconds for stage in stages]
    assert 20 <= sum(sleeps) <= 40
    assert provider_calls["count"] == len(stages)
    assert spy.calls
    assert trace.status == "passed"
    _validate_schema(trace.to_payload())


async def test_live_provider_takes_precedence_over_the_synthetic_factory() -> None:
    def synthetic(_stage):
        raise AssertionError("the synthetic factory must not be used")

    async def provider():
        return _context_factory()(None)

    trace = await RoomLightingLiveTestRunner(
        sleep=lambda _seconds: asyncio.sleep(0)
    ).run(
        _config(),
        mode="safe",
        correlation_id="live-safe-honest-1",
        context_factory=synthetic,
        context_provider=provider,
    )
    assert trace.status == "passed"
    assert trace.steps
