"""Lighting plugin unit tests (HH-014.b, T01/T02 on profile L-SYN-1).

The plugin converts normalized core observations into engine context and
returns proposals — without HA, storage or dispatch.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo

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
