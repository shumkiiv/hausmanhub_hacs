"""Tests for the bounded room lighting live test runner."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, time, timezone

from custom_components.hausman_hub.application.room_lighting_live_test import (
    LIVE_TEST_STAGE_KEYS,
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
                },
                {
                    "id": "light_mirror",
                    "name": "Зеркало",
                    "kind": "light",
                    "entityId": "light.demo_mirror",
                    "role": "mirror",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                },
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
                "targets": {"lightTargets": ["light_mirror"], "groupIds": [], "roles": []},
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
                "targets": {"lightTargets": ["light_mirror"], "groupIds": [], "roles": []},
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
            lights=(
                LightSnapshot("light_main", SensorState.OFF, now),
                LightSnapshot("light_mirror", SensorState.OFF, now),
            ),
        )

    return factory


class _SpyExecutor:
    def __init__(self, receipt: dict[str, object] | None = None) -> None:
        self.calls: list[object] = []
        self._receipt = receipt or {"confirmed": True}

    async def __call__(self, command: object) -> dict[str, object]:
        self.calls.append(command)
        return dict(self._receipt)


def test_build_stages_covers_all_and_is_near_30_seconds() -> None:
    stages = build_stages(_config())
    keys = {stage.key for stage in stages}
    expected = {
        "resolve_devices",
        "snapshot_state",
        "presence_on",
        "schedule_on_presence",
        "schedule_always",
        "schedule_night_light",
        "schedule_off",
        "lux_correction",
        "absence_dimming",
        "manual_off_protection",
        "away_room_off",
        "restore_state",
    }
    assert expected <= keys
    assert expected <= set(LIVE_TEST_STAGE_KEYS)
    total = sum(stage.duration_seconds for stage in stages)
    assert 20 <= total <= 40
    for stage in stages:
        assert stage.title and stage.comment and stage.action
        assert _CYRILLIC.search(stage.title)
        assert _CYRILLIC.search(stage.comment)
        assert stage.duration_seconds >= 2


async def test_safe_run_never_calls_executor() -> None:
    spy = _SpyExecutor()
    trace = await RoomLightingLiveTestRunner().run(
        _config(),
        mode="safe",
        correlation_id="live-safe-001",
        executor=spy,
        context_factory=_context_factory(),
    )

    assert spy.calls == []
    assert trace.commands_sent == 0
    assert trace.receipts == ()
    assert trace.status == "completed"
    assert trace.mode == "safe"
    assert trace.steps
    assert trace.correlation_id == "live-safe-001"
    assert all(step.commands for step in trace.steps if step.key == "presence_on")


async def test_real_run_dispatches_and_records_receipts() -> None:
    spy = _SpyExecutor({"confirmed": True, "action": "turn_on"})
    trace = await RoomLightingLiveTestRunner().run(
        _config(),
        mode="real",
        correlation_id="live-real-001",
        executor=spy,
        context_factory=_context_factory(),
    )

    assert spy.calls
    assert trace.commands_sent == len(spy.calls)
    assert trace.commands_sent > 0
    assert trace.receipts
    assert trace.receipts[0]["confirmed"] is True
    assert trace.status == "completed"


async def test_real_mode_requires_executor() -> None:
    try:
        await RoomLightingLiveTestRunner().run(
            _config(),
            mode="real",
            correlation_id="live-real-002",
        )
    except RoomLightingLiveTestViolation:
        return
    raise AssertionError("real mode without executor was accepted")


async def test_cancel_before_run_stops_it() -> None:
    cancel_event = asyncio.Event()
    cancel_event.set()
    trace = await RoomLightingLiveTestRunner().run(
        _config(),
        mode="safe",
        correlation_id="live-cancel-001",
        cancel_event=cancel_event,
        context_factory=_context_factory(),
    )

    assert trace.status == "cancelled"
    assert trace.commands_sent == 0
    assert len(trace.steps) == 1
    assert trace.steps[0].status == "cancelled"


async def test_cancel_between_stages_stops_it() -> None:
    cancel_event = asyncio.Event()
    calls = {"count": 0}

    def factory(stage):
        calls["count"] += 1
        if calls["count"] == 2:
            cancel_event.set()
        return _context_factory()(stage)

    trace = await RoomLightingLiveTestRunner().run(
        _config(),
        mode="safe",
        correlation_id="live-cancel-002",
        cancel_event=cancel_event,
        context_factory=factory,
    )

    assert trace.status == "cancelled"
    assert len(trace.steps) < len(build_stages(_config()))
