"""Lighting plugin unit tests (HH-014.b, T01/T02 on profile L-SYN-1).

The plugin converts normalized core observations into engine context and
returns proposals — without HA, storage or dispatch.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.domain.lighting_plugin import LightingPlugin
from custom_components.hausman_hub.domain.core.models import (
    InputFrame,
    Observation,
    ObservationAttribution,
    Evidence,
    Quality,
)
from custom_components.hausman_hub.domain.room_lighting import (
    config_from_payload,
)

MODULE_ID = "room_lighting.test_room"
TZ = ZoneInfo("Asia/Omsk")


def _config():
    payload = {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": "test_room",
        "name": "Тест",
        "version": 1,
        "devices": {
            "sensors": [
                {
                    "id": "sensor_presence",
                    "name": "Присутствие",
                    "kind": "presence",
                    "entityId": "binary_sensor.test_presence",
                    "autoAdoptOverride": None,
                },
                {
                    "id": "sensor_motion",
                    "name": "Движение",
                    "kind": "motion",
                    "entityId": "binary_sensor.test_motion",
                    "autoAdoptOverride": None,
                },
            ],
            "light_targets": [
                {
                    "id": "test_mirror",
                    "name": "Зеркало",
                    "kind": "light",
                    "entityId": "light.test_mirror",
                    "role": "mirror",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                },
                {
                    "id": "test_main",
                    "name": "Основной",
                    "kind": "light",
                    "entityId": "light.test_main",
                    "role": "main",
                    "groupId": None,
                    "brightness": True,
                    "color_temperature": False,
                    "autoAdoptOverride": None,
                },
            ],
            "power_switch": None,
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_mirror",
                "title": "Зеркало утро",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "07:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["test_mirror"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 80,
                    "colorTemperature": None,
                    "fade": False,
                    "mode": "on_presence",
                },
            },
            {
                "id": "sch_main",
                "title": "Основной день",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["test_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 80,
                    "colorTemperature": None,
                    "fade": False,
                    "mode": "on_presence",
                },
            },
            {
                "id": "sch_off",
                "title": "Выключение",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "23:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["test_mirror", "test_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": None,
                    "colorTemperature": None,
                    "fade": False,
                    "mode": "off",
                },
            },
        ],
        "switchBindings": [],
        "illumination": None,
        "dimming": {"enabled": False},
        "manualOffProtection": {"enabled": True, "minimumIntervalSeconds": 600},
        "awayBehavior": {"mode": "none"},
        "autoAdopt": False,
        "updatedAt": 1,
        "overrides": {},
    }
    return config_from_payload(payload)


def _plugin() -> LightingPlugin:
    return LightingPlugin(
        module_id=MODULE_ID,
        config=_config(),
        timezone=TZ,
        sunrise=time(8, 0),
        sunset=time(18, 0),
    )


def _observation(
    resource_id: str,
    property_name: str,
    value,
    mono_ms: int,
):
    return Observation(
        observation_id=f"obs-{resource_id}-{property_name}-{mono_ms}",
        resource_id=resource_id,
        property=property_name,
        value=value,
        unit="bool" if isinstance(value, bool) else "pct",
        received_at_utc_ms=mono_ms,
        received_at_mono_ms=mono_ms,
        boot_id="test.boot1",
        source_at_utc_ms=None,
        source_sequence=None,
        source_id="test.ha",
        source_epoch="test.epoch",
        quality=Quality.FRESH,
        evidence_kind=Evidence.DEVICE_REPORT,
        binding_revision=1,
        observation_revision=mono_ms,
        related_operation_id=None,
        attribution=ObservationAttribution.UNKNOWN,
    )


def _frame(now_utc_ms: int, observations):
    return InputFrame(
        now_utc_ms=now_utc_ms,
        now_mono_ms=now_utc_ms,
        boot_id="test.boot1",
        event_id="ev-test",
        config_revision=1,
        observations=tuple(observations),
        resource_controls=(),
        related_scope_revisions={},
        inbound_needs=(),
    )


from datetime import datetime, timezone

# 2026-09-26 07:00 Asia/Omsk (UTC+6) == 01:00 UTC
MORNING_UTC_MS = int(
    datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc).timestamp() * 1000
)


def test_t01_schedule_turns_mirror_on_at_boundary():
    plugin = _plugin()
    # Before the boundary the schedule entry is inactive: no proposals.
    before = _frame(MORNING_UTC_MS - 1000, [
        _observation("test_mirror", "on", False, MORNING_UTC_MS - 1000),
        _observation("sensor_presence", "occupancy", True, MORNING_UTC_MS - 1000),
    ])
    evaluation_before = plugin.evaluate(before, None)
    assert evaluation_before.proposals == ()
    # At the boundary with active presence: one mirror proposal.
    at = _frame(MORNING_UTC_MS, [
        _observation("test_mirror", "on", False, MORNING_UTC_MS),
        _observation("sensor_presence", "occupancy", True, MORNING_UTC_MS),
    ])
    evaluation_at = plugin.evaluate(at, None)
    proposals = evaluation_at.proposals
    assert len(proposals) == 1
    assert proposals[0].resource_id == "test_mirror"
    assert proposals[0].target.on is True


def test_t02_manual_mirror_on_blocks_the_whole_branch():
    plugin = _plugin()
    # The person already turned the mirror on manually before the
    # boundary: the automatic branch produces zero proposals.
    at = _frame(MORNING_UTC_MS, [
        _observation("test_mirror", "on", True, MORNING_UTC_MS),
        _observation("sensor_presence", "occupancy", False, MORNING_UTC_MS),
    ])
    evaluation = plugin.evaluate(at, None)
    assert evaluation.proposals == ()


def test_evaluation_is_deterministic():
    plugin = _plugin()
    frame = _frame(MORNING_UTC_MS, [
        _observation("test_mirror", "on", False, MORNING_UTC_MS),
        _observation("sensor_presence", "occupancy", False, MORNING_UTC_MS),
    ])
    first = plugin.evaluate(frame, None)
    plugin2 = _plugin()
    second = plugin2.evaluate(frame, None)
    assert [p.target for p in first.proposals] == [
        p.target for p in second.proposals
    ]
    assert [p.reason_code for p in first.proposals] == [
        p.reason_code for p in second.proposals
    ]


def test_sensor_ttl_default_and_duplicate_do_not_refresh_continuity():
    plugin = _plugin()
    first = plugin.evaluate(_frame(0, [
        _observation("sensor_presence", "occupancy", False, 0),
    ]), None)
    original = first.next_memory.sensor_continuity[0]
    duplicate = plugin.evaluate(_frame(1_000, [
        _observation("sensor_presence", "occupancy", False, 0),
    ]), first.next_memory)
    retained = duplicate.next_memory.sensor_continuity[0]
    assert original[8:] == (60_000, 0, 0)
    assert retained[8:] == original[8:]


def test_same_lineage_older_sequence_does_not_refresh_continuity():
    plugin = _plugin()
    first = plugin.evaluate(_frame(0, [replace(
        _observation("sensor_presence", "occupancy", False, 0),
        source_sequence=41,
    )]), None)
    older = plugin.evaluate(_frame(1_000, [replace(
        _observation("sensor_presence", "occupancy", False, 1_000),
        source_sequence=1,
    )]), first.next_memory)
    assert older.next_memory.sensor_continuity[0][8:] == (60_000, 0, 0)


def test_sensor_continuity_resets_after_a_configured_90s_gap():
    plugin = LightingPlugin(
        module_id=MODULE_ID,
        config=_config(),
        timezone=TZ,
        sunrise=time(8, 0),
        sunset=time(18, 0),
        sensor_max_age_ms={"sensor_presence": 90_000, "sensor_motion": 90_000},
    )
    first = plugin.evaluate(_frame(0, [
        _observation("sensor_presence", "occupancy", False, 0),
    ]), None)
    continued = plugin.evaluate(_frame(60_000, [
        _observation("sensor_presence", "occupancy", False, 60_000),
    ]), first.next_memory)
    reset = plugin.evaluate(_frame(150_001, [
        _observation("sensor_presence", "occupancy", False, 150_001),
    ]), continued.next_memory)
    assert continued.next_memory.sensor_continuity[0][9] == 0
    assert reset.next_memory.sensor_continuity[0][9] == 150_001


@pytest.mark.parametrize("rollover", ("boot", "config", "binding"))
def test_lineage_rollover_resets_even_for_an_equal_or_lower_sequence(rollover):
    plugin = _plugin()
    initial_observation = replace(
        _observation("sensor_presence", "occupancy", False, 0),
        source_sequence=41,
        observation_id="producer-1",
    )
    first = plugin.evaluate(_frame(0, [initial_observation]), None)
    next_observation = replace(
        _observation("sensor_presence", "occupancy", False, 1_000),
        source_sequence=1,
        observation_id="producer-1",
    )
    next_frame = _frame(1_000, [next_observation])
    if rollover == "boot":
        next_observation = replace(next_observation, boot_id="test.boot2")
        next_frame = replace(next_frame, boot_id="test.boot2", observations=(next_observation,))
    elif rollover == "config":
        next_frame = replace(next_frame, config_revision=2)
    else:
        next_observation = replace(next_observation, binding_revision=2)
        next_frame = replace(next_frame, observations=(next_observation,))
    result = plugin.evaluate(next_frame, first.next_memory)
    continuity = result.next_memory.sensor_continuity[0]
    assert continuity[6] == 1
    assert continuity[9] == 1_000
