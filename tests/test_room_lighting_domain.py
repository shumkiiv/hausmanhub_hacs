"""Domain tests for the room lighting configuration model."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from custom_components.hausman_hub.domain.room_lighting import (
    Anchor,
    AnchorKind,
    RoomLightingViolation,
    config_from_payload,
    resolve_anchor_time,
    room_lighting_violations,
)


def _payload() -> dict[str, object]:
    return {
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
                    "id": "light_demo_main",
                    "name": "Люстра",
                    "kind": "light",
                    "entityId": "light.demo_main",
                    "role": "main",
                    "groupId": "grp_demo_main",
                    "brightness": True,
                    "color_temperature": True,
                    "autoAdoptOverride": None,
                },
                {
                    "id": "light_demo_spots",
                    "name": "Точки",
                    "kind": "switch",
                    "entityId": "switch.demo_spots",
                    "role": "accent",
                    "groupId": "grp_demo_spots",
                    "brightness": False,
                    "color_temperature": False,
                    "autoAdoptOverride": False,
                },
            ],
            "power_switch": {
                "id": "sw_demo_power",
                "name": "Питание",
                "entityId": "switch.demo_power",
                "autoAdoptOverride": None,
            },
            "wireless_switches": [
                {
                    "id": "sw_demo_wall",
                    "name": "Настенный",
                    "entityId": "sensor.demo_wall_action",
                    "buttons": ["left", "right"],
                    "pressTypes": ["single", "double"],
                }
            ],
            "selectAll": False,
        },
        "schedule": [
            {
                "id": "sch_evening",
                "title": "Вечер",
                "when": {
                    "daysOfWeek": "all",
                    "holiday": False,
                    "anchor": {"kind": "sunset", "offsetMinutes": -30},
                },
                "targets": {
                    "lightTargets": ["light_demo_main"],
                    "groupIds": ["grp_demo_main"],
                    "roles": [],
                },
                "how": {
                    "brightness": 0,
                    "colorTemperature": None,
                    "fade": True,
                    "mode": "on_presence",
                },
            }
        ],
        "switchBindings": [
            {
                "switchId": "sw_demo_wall",
                "button": "left",
                "pressType": "single",
                "action": "toggle",
                "targets": {
                    "lightTargets": ["light_demo_main"],
                    "groupIds": [],
                    "roles": [],
                },
            }
        ],
        "illumination": {
            "sensor": "sensor.demo_lux",
            "calibration": {"offset": 0, "multiplier": 1},
            "hysteresis": 5,
            "minLux": 10,
            "maxLux": 20000,
            "thresholds": [
                {"lux": 50, "brightness": 80, "colorTemperature": 3000, "modifier": None}
            ],
            "failClosed": True,
        },
        "dimming": {
            "enabled": True,
            "onAbsence": True,
            "fadeSeconds": 5,
            "targetPercent": 0,
        },
        "manualOffProtection": {
            "enabled": True,
            "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence",
            "stableAbsenceSeconds": 30,
            "priority": "manual_above_auto",
        },
        "awayBehavior": {
            "mode": "room_off",
            "return": {"restore": "by_current_conditions"},
        },
        "autoAdopt": True,
        "updatedAt": 1757000000,
        "templateId": None,
        "overrides": {},
    }


def _with_schedule(payload: dict[str, object], entry: dict[str, object]) -> dict[str, object]:
    payload["schedule"] = [entry]
    return payload


def test_full_payload_round_trips() -> None:
    config = config_from_payload(_payload())

    assert config.room_id == "room_demo_entry"
    assert config.auto_adopt is True
    assert len(config.devices.sensors) == 2
    assert len(config.devices.light_targets) == 2
    assert len(config.switch_bindings) == 1
    assert config.away_behavior.mode.value == "room_off"
    assert config.away_behavior.return_ is not None

    encoded = config.to_dict()
    assert encoded["roomId"] == "room_demo_entry"
    assert config_from_payload(encoded).to_dict() == encoded


def test_brightness_zero_is_not_absence_and_none_is_preserved() -> None:
    config = config_from_payload(_payload())
    how = config.schedule[0].how
    assert how.brightness == 0
    assert how.color_temperature is None
    assert config.to_dict()["schedule"][0]["how"]["brightness"] == 0  # type: ignore[index]
    assert config.to_dict()["schedule"][0]["how"]["colorTemperature"] is None  # type: ignore[index]

    payload = _payload()
    payload["schedule"][0]["how"]["brightness"] = 100  # type: ignore[index]
    assert config_from_payload(payload).schedule[0].how.brightness == 100


@pytest.mark.parametrize("brightness", [-1, 101])
def test_brightness_out_of_bounds_is_rejected(brightness: int) -> None:
    payload = _payload()
    payload["schedule"][0]["how"]["brightness"] = brightness  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


@pytest.mark.parametrize("value", [1999, 6501])
def test_kelvin_out_of_bounds_is_rejected(value: int) -> None:
    payload = _payload()
    payload["schedule"][0]["how"]["colorTemperature"] = value  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_null_colour_temperature_stays_null() -> None:
    payload = _payload()
    payload["schedule"][0]["how"]["brightness"] = None  # type: ignore[index]
    payload["schedule"][0]["how"]["colorTemperature"] = None  # type: ignore[index]
    config = config_from_payload(payload)
    assert config.schedule[0].how.brightness is None
    assert config.schedule[0].how.color_temperature is None


def test_invalid_enums_are_rejected() -> None:
    payload = _payload()
    payload["switchBindings"][0]["pressType"] = "triple"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)

    payload = _payload()
    payload["switchBindings"][0]["action"] = "set_mood"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)

    payload = _payload()
    payload["manualOffProtection"]["releaseMode"] = "always"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)

    payload = _payload()
    payload["manualOffProtection"]["priority"] = "auto_above_manual"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_illumination_fails_closed() -> None:
    payload = _payload()
    payload["illumination"]["failClosed"] = False  # type: ignore[index]
    assert room_lighting_violations(payload)
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_unregulated_switch_target_cannot_expose_brightness() -> None:
    payload = _payload()
    payload["devices"]["light_targets"][1]["brightness"] = True  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_schedule_cannot_set_brightness_on_unregulated_target() -> None:
    payload = _with_schedule(
        _payload(),
        {
            "id": "sch_bad",
            "when": {
                "daysOfWeek": "all",
                "holiday": False,
                "anchor": {"kind": "sunset", "offsetMinutes": 0},
            },
            "targets": {
                "lightTargets": ["light_demo_spots"],
                "groupIds": [],
                "roles": [],
            },
            "how": {
                "brightness": 50,
                "colorTemperature": None,
                "fade": True,
                "mode": "on_presence",
            },
        },
    )
    violations = room_lighting_violations(payload)
    assert any("unregulated" in item for item in violations)
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_unknown_references_are_rejected() -> None:
    payload = _payload()
    payload["schedule"][0]["targets"]["lightTargets"] = ["light_unknown"]  # type: ignore[index]
    assert any("unknown light target" in item for item in room_lighting_violations(payload))

    payload = _payload()
    payload["switchBindings"][0]["switchId"] = "sw_unknown"  # type: ignore[index]
    assert any(
        "unknown wireless switch" in item for item in room_lighting_violations(payload)
    )


def test_unconfirmed_press_type_is_rejected() -> None:
    payload = _payload()
    payload["switchBindings"][0]["pressType"] = "long"  # type: ignore[index]
    violations = room_lighting_violations(payload)
    assert any("unconfirmed press type" in item for item in violations)


def test_device_trigger_switch_and_binding_round_trip() -> None:
    payload = _payload()
    payload["devices"]["wireless_switches"].append(  # type: ignore[index]
        {
            "id": "sw_demo_mirror",
            "name": "Зеркало (device trigger)",
            "deviceId": "device_demo_mirror",
            "triggerSubtypes": ["1_single", "1_double"],
            "buttons": ["left", "right"],
            "pressTypes": ["single", "double"],
        }
    )
    payload["switchBindings"].append(  # type: ignore[index]
        {
            "switchId": "sw_demo_mirror",
            "triggerSubtype": "1_single",
            "action": "toggle",
            "targets": {
                "lightTargets": ["light_demo_main"],
                "groupIds": [],
                "roles": [],
            },
        }
    )

    config = config_from_payload(payload)
    device = config.devices.wireless_switch("sw_demo_mirror")
    assert device is not None
    assert device.device_id == "device_demo_mirror"
    assert device.trigger_subtypes == ("1_single", "1_double")
    binding = config.switch_bindings[-1]
    assert binding.trigger_subtype == "1_single"
    assert binding.button is None
    assert binding.press_type is None

    encoded = config.to_dict()
    assert config_from_payload(encoded).to_dict() == encoded
    assert encoded["switchBindings"][-1]["triggerSubtype"] == "1_single"  # type: ignore[index]
    assert (
        encoded["devices"]["wireless_switches"][-1]["deviceId"]  # type: ignore[index]
        == "device_demo_mirror"
    )


def test_binding_without_button_or_trigger_is_rejected() -> None:
    payload = _payload()
    del payload["switchBindings"][0]["button"]  # type: ignore[index]
    del payload["switchBindings"][0]["pressType"]  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_mixed_binding_is_rejected() -> None:
    payload = _payload()
    payload["devices"]["wireless_switches"][0]["triggerSubtypes"] = ["1_single"]  # type: ignore[index]
    payload["switchBindings"][0]["triggerSubtype"] = "1_single"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_unconfirmed_trigger_subtype_is_rejected() -> None:
    payload = _payload()
    payload["devices"]["wireless_switches"][0]["triggerSubtypes"] = ["1_single"]  # type: ignore[index]
    binding = payload["switchBindings"][0]  # type: ignore[index]
    binding.pop("button", None)
    binding.pop("pressType", None)
    binding["triggerSubtype"] = "2_single"
    violations = room_lighting_violations(payload)
    assert any("unconfirmed trigger subtype" in item for item in violations)


def test_malformed_trigger_subtype_is_rejected() -> None:
    payload = _payload()
    payload["devices"]["wireless_switches"][0]["triggerSubtypes"] = ["1_single"]  # type: ignore[index]
    binding = payload["switchBindings"][0]  # type: ignore[index]
    binding.pop("button", None)
    binding.pop("pressType", None)
    binding["triggerSubtype"] = "bad subtype"
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_unknown_optional_fields_are_ignored() -> None:
    payload = _payload()
    payload["futureTopLevel"] = {"anything": True}
    payload["devices"]["futureDeviceBlock"] = {}  # type: ignore[index]
    payload["schedule"][0]["how"]["futureHow"] = 7  # type: ignore[index]

    config = config_from_payload(payload)

    assert config.room_id == "room_demo_entry"
    assert "futureTopLevel" not in config.to_dict()


def test_auto_adopt_override_inherits_room_default() -> None:
    config = config_from_payload(_payload())
    assert config.effective_auto_adopt(None) is True
    assert config.effective_auto_adopt(False) is False

    payload = _payload()
    payload["autoAdopt"] = False
    assert config_from_payload(payload).effective_auto_adopt(None) is False


def test_resolve_anchor_time_uses_passed_sun_and_timezone() -> None:
    tz = timezone(timedelta(hours=3))
    day = date(2026, 9, 11)

    sunrise = resolve_anchor_time(
        Anchor(kind=AnchorKind.SUNRISE, offset_minutes=30),
        day=day,
        sunrise=time(5, 0),
        sunset=time(19, 0),
        home_timezone=tz,
    )
    sunset = resolve_anchor_time(
        Anchor(kind=AnchorKind.SUNSET, offset_minutes=-30),
        day=day,
        sunrise=time(5, 0),
        sunset=time(19, 0),
        home_timezone=tz,
    )
    fixed = resolve_anchor_time(
        Anchor(kind=AnchorKind.FIXED, time="23:00", offset_minutes=0),
        day=day,
        sunrise=time(5, 0),
        sunset=time(19, 0),
        home_timezone=tz,
    )

    assert sunrise == datetime(2026, 9, 11, 5, 30, tzinfo=tz)
    assert sunset == datetime(2026, 9, 11, 18, 30, tzinfo=tz)
    assert fixed == datetime(2026, 9, 11, 23, 0, tzinfo=tz)


def test_min_on_seconds_round_trip_and_bounds() -> None:
    payload = _payload()
    payload["schedule"][0]["how"]["minOnSeconds"] = 600  # type: ignore[index]
    config = config_from_payload(payload)
    assert config.schedule[0].how.min_on_seconds == 600
    assert config.to_dict()["schedule"][0]["how"]["minOnSeconds"] == 600  # type: ignore[index]

    for bad in (-1, 86401):
        invalid = _payload()
        invalid["schedule"][0]["how"]["minOnSeconds"] = bad  # type: ignore[index]
        with pytest.raises(RoomLightingViolation):
            config_from_payload(invalid)


def test_commands_enabled_defaults_to_shadow_and_round_trips() -> None:
    payload = _payload()
    payload.pop("commandsEnabled", None)
    config = config_from_payload(payload)
    assert config.commands_enabled is False
    assert config.to_dict()["commandsEnabled"] is False

    payload["commandsEnabled"] = True
    config = config_from_payload(payload)
    assert config.commands_enabled is True
    encoded = config.to_dict()
    assert encoded["commandsEnabled"] is True
    assert config_from_payload(encoded).to_dict() == encoded


def test_commands_enabled_must_be_boolean() -> None:
    payload = _payload()
    payload["commandsEnabled"] = "yes"
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_color_temp_inverted_defaults_false_and_round_trips() -> None:
    payload = _payload()
    config = config_from_payload(payload)
    assert config.devices.light_targets[0].color_temp_inverted is False
    encoded = config.to_dict()
    assert encoded["devices"]["light_targets"][0]["colorTempInverted"] is False  # type: ignore[index]
    assert config_from_payload(encoded).to_dict() == encoded

    payload["devices"]["light_targets"][0]["colorTempInverted"] = True  # type: ignore[index]
    inverted = config_from_payload(payload)
    assert inverted.devices.light_targets[0].color_temp_inverted is True
    encoded = inverted.to_dict()
    assert encoded["devices"]["light_targets"][0]["colorTempInverted"] is True  # type: ignore[index]
    assert config_from_payload(encoded).to_dict() == encoded


def test_color_temp_inverted_must_be_boolean() -> None:
    payload = _payload()
    payload["devices"]["light_targets"][0]["colorTempInverted"] = "yes"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_timers_absence_seconds_round_trips() -> None:
    assert config_from_payload(_payload()).timers is None

    payload = _payload()
    payload["timers"] = {"absence_seconds": 300, "turn_off_seconds": 300}  # type: ignore[index]
    config = config_from_payload(payload)
    assert config.timers is not None
    assert config.timers.absence_seconds == 300
    encoded = config.to_dict()
    assert encoded["timers"] == {"absence_seconds": 300, "turn_off_seconds": 300}  # type: ignore[index]
    assert config_from_payload(encoded).to_dict() == encoded


def test_auto_control_defaults_true_and_round_trips() -> None:
    payload = _payload()
    config = config_from_payload(payload)
    assert config.devices.light_targets[0].auto_control is True
    encoded = config.to_dict()
    assert encoded["devices"]["light_targets"][0]["autoControl"] is True  # type: ignore[index]
    assert config_from_payload(encoded).to_dict() == encoded

    payload["devices"]["light_targets"][0]["autoControl"] = False  # type: ignore[index]
    manual = config_from_payload(payload)
    assert manual.devices.light_targets[0].auto_control is False
    encoded = manual.to_dict()
    assert encoded["devices"]["light_targets"][0]["autoControl"] is False  # type: ignore[index]
    assert config_from_payload(encoded).to_dict() == encoded


def test_auto_control_must_be_boolean() -> None:
    payload = _payload()
    payload["devices"]["light_targets"][0]["autoControl"] = "yes"  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)


def test_manual_protection_accepts_fifteen_second_minimum() -> None:
    payload = _payload()
    payload["manualOffProtection"]["minimumIntervalSeconds"] = 15  # type: ignore[index]
    payload["manualOffProtection"]["stableAbsenceSeconds"] = 15  # type: ignore[index]
    config = config_from_payload(payload)
    assert config.manual_off_protection.minimum_interval_seconds == 15
    assert config.manual_off_protection.stable_absence_seconds == 15
    encoded = config.to_dict()
    assert encoded["manualOffProtection"]["minimumIntervalSeconds"] == 15  # type: ignore[index]
    assert config_from_payload(encoded).to_dict() == encoded


def test_manual_protection_rejects_interval_below_fifteen_seconds() -> None:
    payload = _payload()
    payload["manualOffProtection"]["minimumIntervalSeconds"] = 14  # type: ignore[index]
    with pytest.raises(RoomLightingViolation):
        config_from_payload(payload)
