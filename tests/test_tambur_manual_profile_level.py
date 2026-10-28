"""Owner's manual-zero level rule; synthetic HA, no device access."""
from dataclasses import replace
from datetime import datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.domain.light_curve import LightCurveProfile, time_mode_brightness
from test_tambur_manual_pair_reproduction import _paired_trigger_payload
from test_room_lighting_runtime import (
    AWAY_ENTITY_ID, POWER_ENTITY, RoomLightingHaExecutor,
    _FakeDeviceAutomationApi, _FakeHass, _make_runtime,
    _service_context_factory,
)


@pytest.mark.parametrize("local_time,expected", [
    ("00:00", 50), ("06:59", 50), ("07:00", 50),
    ("09:00", 15), ("10:30", 58), ("12:00", 100),
    ("17:59", 100), ("18:00", 40), ("21:00", 14),
    ("22:15", 5), ("23:00", 50), ("23:59", 50),
])
@pytest.mark.parametrize("state,zero", [("off", 0), ("on", 0.0)])
async def test_manual_zero_uses_local_curve_or_fifty_when_time_mode_is_off(local_time, expected, state, zero):
    moment = datetime.fromisoformat("2026-09-11T" + local_time).replace(tzinfo=ZoneInfo("Asia/Omsk"))
    hass = _FakeHass()
    hass.config = SimpleNamespace(time_zone="Asia/Omsk")
    for entity, value, attributes in (
        (AWAY_ENTITY_ID, "off", {}),
        (POWER_ENTITY, "on", {}),
        ("binary_sensor.demo_presence", "off", {}),
        ("light.demo_main", state, {"brightness": zero}),
    ):
        hass.states.set(entity, value, attributes, last_changed=moment)
    payload = _paired_trigger_payload()
    payload["profile"] = "day_curve"
    payload["curveParams"] = {
        "eveningMaxPercent": 40, "neutralKelvin": 3278,
        "eveningWarmKelvin": 3003, "presenceConfirmSeconds": 3,
    }
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
        executor=RoomLightingHaExecutor(), device_automation_api=api,
        now_ms=lambda: int(moment.timestamp() * 1000))
    await runtime.start(hass, "entry")
    try:
        # Startup may already run the automatic curve. The press is a new
        # event whose exact-zero precondition must hold at the press itself.
        hass.states.set("light.demo_main", state, {"brightness": zero}, last_changed=moment)
        before_press = hass.states.get("light.demo_main")
        assert before_press.state == state
        assert type(before_press.attributes["brightness"]) in {int, float}
        assert before_press.attributes["brightness"] == 0
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        commands = [data for domain, service, data in hass.services.calls
                    if domain == "light" and service == "turn_on"]
        assert commands == [{"entity_id": "light.demo_main", "brightness_pct": expected}]
        records = runtime._ownership.snapshots_for(payload["roomId"], {"light_main"})
        assert records[-1].source.value == "manual"
        hass.services.calls.clear()
        await runtime.async_process([payload["roomId"]])
        assert hass.services.calls == []
    finally:
        await runtime.stop()


async def test_parameterized_curve_keeps_previous_manual_zero_level():
    from test_room_lighting_curve_params_runtime import FULL_CURVE

    moment = datetime(2026, 9, 11, 23, tzinfo=ZoneInfo("Asia/Omsk"))
    hass, api = _FakeHass(), _FakeDeviceAutomationApi()
    hass.config = SimpleNamespace(time_zone="Asia/Omsk")
    for entity, value in ((AWAY_ENTITY_ID, "off"), (POWER_ENTITY, "on"),
                          ("binary_sensor.demo_presence", "off")):
        hass.states.set(entity, value, last_changed=moment)
    hass.states.set("light.demo_main", "off", {"brightness": 0}, last_changed=moment)
    payload = _paired_trigger_payload()
    payload["profile"], payload["curveParams"] = "day_curve", FULL_CURVE
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
        executor=RoomLightingHaExecutor(), device_automation_api=api,
        now_ms=lambda: int(moment.timestamp() * 1000))
    await runtime.start(hass, "entry")
    try:
        hass.states.set("light.demo_main", "off", {"brightness": 0}, last_changed=moment)
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [("light", "turn_on", {"entity_id": "light.demo_main", "brightness_pct": 100})]
    finally:
        await runtime.stop()


@pytest.mark.parametrize("clock,sunrise,sunset,expected", [
    (time(9), time(10), time(19), None),
    (time(10), time(10), time(19), 15),
    (time(11), time(10), time(19), 58),
    (time(16), time(7), time(17), 40),
    (time(21), time(7), time(19), 14),
    (time(22), time(7), time(19), 5),
    (time(23), time(7), time(19), None),
])
def test_time_mode_reuses_solar_boundaries_without_occupancy(clock, sunrise, sunset, expected):
    profile = replace(LightCurveProfile(), evening_max_percent=40)
    assert time_mode_brightness(profile, clock, sunrise, sunset) == expected


@pytest.mark.parametrize("change", ["stop", "restart_epoch", "same_millisecond_restart", "config", "state", "writer", "dependencies", "context_error"])
@pytest.mark.parametrize("action", ["turn_on", "toggle"])
async def test_manual_time_level_cancels_stale_press(monkeypatch, change, action):
    moment = datetime(2026, 9, 11, 21, tzinfo=ZoneInfo("Asia/Omsk"))
    hass, api = _FakeHass(), _FakeDeviceAutomationApi()
    hass.config = SimpleNamespace(time_zone="Asia/Omsk")
    for entity, value in ((AWAY_ENTITY_ID, "off"), (POWER_ENTITY, "on"),
                          ("binary_sensor.demo_presence", "off")):
        hass.states.set(entity, value, last_changed=moment)
    hass.states.set("light.demo_main", "off", {"brightness": 0}, last_changed=moment)
    payload = _paired_trigger_payload(action=action)
    payload["profile"] = "day_curve"
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
        executor=RoomLightingHaExecutor(), device_automation_api=api,
        now_ms=lambda: int(moment.timestamp() * 1000))
    await runtime.start(hass, "entry")
    original = runtime._state_provider.build_context

    async def changed_context(*args):
        context = await original(*args)
        if change == "context_error":
            raise ValueError("synthetic unavailable context")
        if change == "stop":
            await runtime.stop()
        elif change == "restart_epoch":
            runtime._started_at += 1
        elif change == "same_millisecond_restart":
            runtime._return_intent_epoch += 1
        elif change == "config":
            config = runtime._configs[payload["roomId"]]
            runtime._configs[payload["roomId"]] = replace(config, commands_enabled=False)
        elif change == "state":
            hass.states.set("light.demo_main", "on", {"brightness": 127}, last_changed=moment)
        elif change == "writer":
            monkeypatch.setattr(runtime, "_binding_conflicts_with_reserved_writer", lambda *a, **kw: True)
        elif change == "dependencies":
            assert runtime._power_dependencies() is None
            monkeypatch.setattr(runtime, "_power_dependencies", lambda: {})
        return context

    monkeypatch.setattr(runtime._state_provider, "build_context", changed_context)
    try:
        hass.states.set("light.demo_main", "off", {"brightness": 0}, last_changed=moment)
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == []
    finally:
        await runtime.stop()


@pytest.mark.parametrize("zone,expected", [("UTC", 100), ("Asia/Omsk", 14)])
async def test_manual_level_uses_home_timezone_and_toggle_on(zone, expected):
    moment = datetime(2026, 9, 11, 15, tzinfo=ZoneInfo("UTC"))
    hass, api = _FakeHass(), _FakeDeviceAutomationApi()
    hass.config = SimpleNamespace(time_zone=zone)
    for entity, value in ((AWAY_ENTITY_ID, "off"), (POWER_ENTITY, "on"),
                          ("binary_sensor.demo_presence", "off")):
        hass.states.set(entity, value, last_changed=moment)
    hass.states.set("light.demo_main", "off", {"brightness": 0}, last_changed=moment)
    payload = _paired_trigger_payload(action="toggle")
    payload["profile"] = "day_curve"
    payload["curveParams"] = {"eveningMaxPercent": 40}
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
        executor=RoomLightingHaExecutor(), device_automation_api=api,
        now_ms=lambda: int(moment.timestamp() * 1000))
    await runtime.start(hass, "entry")
    try:
        hass.states.set("light.demo_main", "off", {"brightness": 0}, last_changed=moment)
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [("light", "turn_on", {"entity_id": "light.demo_main", "brightness_pct": expected})]
    finally:
        await runtime.stop()
