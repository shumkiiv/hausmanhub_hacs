"""Focused reproductions on the confirmed installed RoomLighting sources.

All events are synthetic. These tests do not contact Home Assistant or MQTT.
Power-ON tests describe the installed gap, not an approved positive authority.
"""
from types import SimpleNamespace

import pytest

from test_room_lighting_runtime import (
    AWAY_ENTITY_ID,
    POWER_ENTITY,
    RoomLightingHaExecutor,
    _FakeDeviceAutomationApi,
    _FakeHass,
    _NOW_DT,
    _NOW_MS,
    _ROOM_ID,
    _config_payload,
    _event,
    _make_runtime,
    _service_context_factory,
    _trigger_config_payload,
)


def _seed(hass, state="off", brightness=0):
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=_NOW_DT)
    hass.states.set(AWAY_ENTITY_ID, "off", last_changed=_NOW_DT)
    attributes = {} if brightness is None else {"brightness": brightness}
    hass.states.set("light.demo_main", state, attributes, last_changed=_NOW_DT)
    hass.states.set(POWER_ENTITY, "on", last_changed=_NOW_DT)


def _paired_trigger_payload(action="turn_on"):
    payload = _trigger_config_payload(action=action)
    payload["devices"]["power_switch"] = _config_payload(
        power_switch_entity=POWER_ENTITY
    )["devices"]["power_switch"]
    return payload


@pytest.mark.parametrize("state,action", [("off", "turn_on"), ("on", "turn_on"), ("off", "toggle")])
@pytest.mark.parametrize("zero", [0, 0.0])
async def test_confirmed_manual_on_with_zero_brightness_sends_positive_level(state, action, zero):
    hass = _FakeHass()
    _seed(hass, state, zero)
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=True,
                            payload=_paired_trigger_payload(action=action),
                            executor=RoomLightingHaExecutor(), device_automation_api=api)
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        _, press, _ = api.attached[0]
        await press({}, None)
        light_calls = [data for domain, service, data in hass.services.calls
                       if domain == "light" and service == "turn_on"]
        assert len(light_calls) == 1
        assert light_calls[0].get("brightness_pct") == 100
        assert "color_temp_kelvin" not in light_calls[0]
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
        assert records[-1].source.value == "manual"
    finally:
        await runtime.stop()


@pytest.mark.parametrize("brightness", [127, 255, None, "0", False, -1, float("nan")])
async def test_manual_on_without_exact_numeric_zero_keeps_parameters(brightness):
    hass = _FakeHass()
    _seed(hass, "off", brightness)
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=True,
                            payload=_paired_trigger_payload(),
                            executor=RoomLightingHaExecutor(), device_automation_api=api)
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [("light", "turn_on", {"entity_id": "light.demo_main"})]
    finally:
        await runtime.stop()


async def test_explicit_manual_brightness_wins_over_zero_restoration():
    hass = _FakeHass()
    _seed(hass)
    api = _FakeDeviceAutomationApi()
    payload = _paired_trigger_payload()
    payload["switchBindings"][0]["brightness"] = 37
    payload["switchBindings"][0]["colorTemperature"] = 3500
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
                            executor=RoomLightingHaExecutor(), device_automation_api=api)
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [("light", "turn_on", {"entity_id": "light.demo_main", "brightness_pct": 37, "color_temp_kelvin": 3500})]
    finally:
        await runtime.stop()


async def test_toggle_of_already_on_target_stays_manual_off_even_at_zero():
    hass = _FakeHass()
    _seed(hass, "on", 0)
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=True,
                            payload=_paired_trigger_payload(action="toggle"),
                            executor=RoomLightingHaExecutor(), device_automation_api=api)
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [("light", "turn_off", {"entity_id": "light.demo_main"})]
    finally:
        await runtime.stop()


@pytest.mark.parametrize("case", ["unpaired", "unbound_power", "accent", "not_dimmable", "binary", "unknown", "unavailable"])
async def test_zero_restoration_does_not_extend_to_other_targets_or_unknown_state(case):
    hass = _FakeHass()
    _seed(hass, case if case in {"unknown", "unavailable"} else "off", 0)
    payload = _paired_trigger_payload()
    target = payload["devices"]["light_targets"][0]
    entity_id = "light.demo_main"
    if case == "unpaired":
        payload["devices"]["power_switch"] = None
    elif case == "unbound_power":
        payload["devices"]["power_switch"]["entityId"] = None
    elif case == "accent":
        target["role"] = "accent"
    elif case == "not_dimmable":
        target["brightness"] = False
        payload["schedule"][0]["how"]["brightness"] = None
    elif case == "binary":
        target["kind"] = "switch"
        target["entityId"] = entity_id = "switch.demo_main"
        target["brightness"] = False
        target["color_temperature"] = False
        payload["schedule"][0]["how"]["brightness"] = None
        payload["schedule"][0]["how"]["colorTemperature"] = None
        hass.states.set(entity_id, "off", {"brightness": 0}, last_changed=_NOW_DT)
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=True, payload=payload,
                            executor=RoomLightingHaExecutor(), device_automation_api=api)
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [(entity_id.split(".")[0], "turn_on", {"entity_id": entity_id})]
    finally:
        await runtime.stop()


@pytest.mark.parametrize("case", ["shadow", "stopped", "reserved"])
async def test_zero_restoration_preserves_dispatch_boundaries(case):
    hass = _FakeHass()
    _seed(hass)
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=case != "shadow", payload=_paired_trigger_payload(),
                            executor=RoomLightingHaExecutor(), device_automation_api=api,
                            reserved_entity_ids_provider=lambda: ("light.demo_main",) if case == "reserved" else ())
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        if case == "stopped":
            await runtime.stop()
        await api.attached[0][1]({}, None)
        assert hass.services.calls == []
    finally:
        await runtime.stop()


async def test_duplicate_zero_brightness_press_is_not_a_second_command():
    hass = _FakeHass()
    _seed(hass)
    api = _FakeDeviceAutomationApi()
    runtime = _make_runtime(hass, commands_enabled=True, payload=_paired_trigger_payload(),
                            executor=RoomLightingHaExecutor(), device_automation_api=api)
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        await api.attached[0][1]({}, None)
        await api.attached[0][1]({}, None)
        assert hass.services.calls == [("light", "turn_on", {"entity_id": "light.demo_main", "brightness_pct": 100})]
    finally:
        await runtime.stop()


@pytest.mark.parametrize("origin", ["user", "automation", "unknown"])
async def test_installed_power_on_service_has_no_separate_chandelier_command(origin):
    hass = _FakeHass()
    _seed(hass)
    runtime = _make_runtime(hass, commands_enabled=True,
                            payload=_config_payload(power_switch_entity=POWER_ENTITY),
                            executor=RoomLightingHaExecutor())
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        event = _event("switch", "turn_on", POWER_ENTITY)
        if origin != "unknown":
            event.context = SimpleNamespace(id=origin, user_id="synthetic-user" if origin == "user" else None,
                                            parent_id="automation-parent" if origin == "automation" else None)
        runtime._service_event(event)
        assert hass.services.calls == []
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()
    finally:
        await runtime.stop()


@pytest.mark.parametrize("previous", ["off", "unknown", "unavailable", None])
async def test_installed_power_state_on_has_no_positive_manual_dispatch(previous):
    hass = _FakeHass()
    _seed(hass)
    runtime = _make_runtime(hass, commands_enabled=True,
                            payload=_config_payload(power_switch_entity=POWER_ENTITY),
                            executor=RoomLightingHaExecutor())
    await runtime.start(hass, "entry")
    try:
        hass.services.calls.clear()
        runtime._target_last_state[POWER_ENTITY] = previous
        hass.states.set(POWER_ENTITY, "on", last_changed=_NOW_DT)
        runtime._observe_power_transition(POWER_ENTITY)
        assert hass.services.calls == []
        assert runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"}) == ()
    finally:
        await runtime.stop()
