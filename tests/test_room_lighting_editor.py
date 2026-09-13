"""Unit tests for the room lighting editor catalog and safe preview."""

from __future__ import annotations

from datetime import datetime, time, timezone

from custom_components.hausman_hub.application.room_lighting_editor import (
    EditorDevice,
    RoomLightingEditorService,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightSnapshot,
    RoomLightingContext,
    SensorSnapshot,
    SkipReason,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    OwnershipSnapshot,
    OwnershipSource,
    SensorState,
)

_TZ = timezone.utc
_NOW_MS = int(datetime(2026, 9, 11, 10, 0, tzinfo=_TZ).timestamp() * 1000)
_ROOM_ID = "room_demo_entry"


def _payload() -> dict[str, object]:
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": _ROOM_ID,
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
                }
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
            "wireless_switches": [],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_day",
                "title": "День",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "fixed", "time": "09:00", "offsetMinutes": 0},
                },
                "targets": {
                    "lightTargets": ["light_main"],
                    "groupIds": [],
                    "roles": [],
                },
                "how": {
                    "brightness": 60,
                    "colorTemperature": 3000,
                    "fade": True,
                    "mode": "on_presence",
                    "minOnSeconds": 0,
                },
            }
        ],
        "switchBindings": [],
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
        "awayBehavior": {"mode": "none"},
        "autoAdopt": True,
        "updatedAt": 1,
        "overrides": {},
    }


def _context(*, light_on: bool, manual: bool = False) -> RoomLightingContext:
    ownership = (
        (OwnershipSnapshot("light_main", OwnershipSource.MANUAL, True, _NOW_MS),)
        if manual
        else ()
    )
    return RoomLightingContext(
        now=_NOW_MS,
        timezone=_TZ,
        sunrise=time(6, 30),
        sunset=time(20, 30),
        sensors=(
            SensorSnapshot(
                "sensor_demo_presence",
                config_from_payload(_payload()).devices.sensors[0].kind,
                SensorState.ON,
                _NOW_MS,
            ),
        ),
        lights=(
            LightSnapshot(
                "light_main",
                SensorState.ON if light_on else SensorState.OFF,
                _NOW_MS,
                brightness=153,
            ),
        ),
        ownership=ownership,
    )


def _service(context: RoomLightingContext) -> RoomLightingEditorService:
    config = config_from_payload(_payload())
    return RoomLightingEditorService(
        configs={_ROOM_ID: config},
        devices=lambda room_id: (
            EditorDevice(
                entity_id="light.demo_main",
                kind="light",
                user_label="Люстра",
                physical_device_label="Люстра тамбур",
                channel_label="основной канал",
                supports_brightness=True,
                supports_color_temperature=True,
            ),
            EditorDevice(
                entity_id="switch.demo_left",
                kind="switch",
                physical_device_label="Выключатель у двери",
                channel_label="левая клавиша",
                gestures=("single", "double"),
            ),
            EditorDevice(
                entity_id="switch.demo_right",
                kind="switch",
                physical_device_label="Выключатель у двери",
                channel_label="правая клавиша",
                gestures=("single",),
            ),
        ),
        context=context,
    )


def test_catalog_prefers_user_name_and_disambiguates_same_device_lines() -> None:
    catalog = _service(_context(light_on=False)).catalog(_ROOM_ID)
    labels = {device["label"] for device in catalog["devices"]}
    assert "Люстра" in labels
    assert "Выключатель у двери · левая клавиша" in labels
    assert "Выключатель у двери · правая клавиша" in labels
    assert catalog["contract"]["name"] == "hausman-hub-room-lighting-editor-catalog"
    assert all(device["id"].islower() for device in catalog["devices"])


def test_preview_returns_section_issue_without_persisting_or_dispatching() -> None:
    service = _service(_context(light_on=False))
    invalid = _payload()
    invalid["devices"]["sensors"][0]["id"] = "Bad ID"
    preview = service.preview(_ROOM_ID, invalid)
    assert preview["safe"] is False
    assert preview["sectionIssues"], "invalid draft must publish a section issue"
    issue = preview["sectionIssues"][0]
    assert issue["section"] == "inputs"
    assert "sensor" in issue["message"].lower()
    # The service has no storage or executor dependency at all: the preview
    # payload is the only observable effect.
    assert "commands" not in preview


def test_preview_reports_manual_ownership_as_a_reason_to_skip_action() -> None:
    preview = _service(_context(light_on=True, manual=True)).preview(
        _ROOM_ID, _payload()
    )
    assert preview["safe"] is True
    reasons = [
        step["detail"]
        for step in preview["steps"]
        if SkipReason.MANUAL_OWNERSHIP.value in step["detail"]
    ]
    assert reasons, preview["steps"]
    assert any("вручную" in detail for detail in reasons)
