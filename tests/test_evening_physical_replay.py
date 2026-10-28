"""Evening replay through the installed physical input and HA projection."""

from datetime import datetime, timezone
from dataclasses import replace

import pytest

import test_lighting_mqtt_observations as physical
from test_evening_mirror_handover import _configuration
from test_room_lighting_runtime import _FakeHass, _make_runtime
from custom_components.hausman_hub.application.room_lighting_ha_state import RoomLightingHaStateProvider
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, evaluate_curve_room
from custom_components.hausman_hub.domain.room_lighting_ownership import OwnershipSnapshot, OwnershipSource, SensorState


def replay():
    start = int(datetime(2026, 10, 16, 22, 20, tzinfo=timezone.utc).timestamp() * 1000)
    port, native, clock = physical.make_case(now_ms=start)
    payload = _configuration().to_dict()
    channels = {"light_main": 3, "light_mirror": 2, "light_spots": 0}
    for target in payload["devices"]["light_targets"]:
        target["entityId"] = physical.ROLLOUT_BINDINGS[channels[target["id"]]].entity_id
        if target["id"] != "light_main":
            target.update(kind="switch", brightness=False, color_temperature=False)
    payload["devices"]["power_switch"] = {
        "id": "power", "name": "Питание", "entityId": physical.ROLLOUT_BINDINGS[1].entity_id,
        "autoAdoptOverride": None,
    }
    config = config_from_payload(payload)
    hass = _FakeHass()
    hass.states.set("binary_sensor.demo_presence", "off", last_changed=datetime.fromtimestamp(clock["now"] / 1000, timezone.utc))
    provider = RoomLightingHaStateProvider(
        state_reader=port.read_state, now_ms=lambda: clock["now"],
        ownership_provider=lambda _: tuple(OwnershipSnapshot(target.id, OwnershipSource.AUTO, True, clock["now"] - 1000)
                                          for target in config.devices.light_targets),
    )
    # Only a genuine physical report, not HA last_changed, renews each channel.
    session = None
    for sequence, channel in enumerate((3, 0, 2), 1):
        body = physical.report(clock, channel=channel, sequence=sequence, session=session)
        session = body["session"]
        physical.feed(port, body)
    return provider, config, hass, clock, port, native


async def test_fresh_mirror_confirmation_is_not_rewritten_by_unknown_power():
    provider, config, hass, clock, _, _ = replay()
    context = await provider.build_context(hass, config, clock["now"])
    assert context.light("light_mirror").state is SensorState.ON
    decision = evaluate_curve_room(config, context, presence_confirmed=False, absence_seconds=0)
    assert [(c.target_id, c.action) for c in decision.commands] == [
        ("light_main", LightAction.TURN_OFF), ("light_spots", LightAction.TURN_OFF),
    ]


@pytest.mark.parametrize("presence", ["unknown", "unavailable"])
async def test_five_minute_expiration_never_switches_back_to_chandelier(presence):
    provider, config, hass, clock, _, _ = replay()
    clock["now"] += 300_001
    hass.states.set("binary_sensor.demo_presence", presence, last_changed=datetime.fromtimestamp(clock["now"] / 1000, timezone.utc))
    context = await provider.build_context(hass, config, clock["now"])
    assert context.light("light_mirror").state is SensorState.UNKNOWN
    decision = evaluate_curve_room(config, context, presence_confirmed=False, absence_seconds=None)
    assert not decision.commands


@pytest.mark.parametrize("power", ["on", "off", "unknown"])
@pytest.mark.parametrize("target_state", ["fresh", "stale", "unavailable"])
@pytest.mark.parametrize("second_main", [False, True])
async def test_power_proof_never_overrides_independent_targets(power, target_state, second_main):
    provider, config, hass, clock, port, native = replay()
    if second_main:
        from custom_components.hausman_hub.domain.room_lighting import LightRole
        config = replace(config, devices=replace(config.devices, light_targets=tuple(
            replace(target, role=LightRole.MAIN) if target.id == "light_spots" else target
            for target in config.devices.light_targets)))
    if target_state == "stale":
        clock["now"] += 300_001
    if target_state == "unavailable":
        for channel in (3, 0, 2):
            native[physical.ROLLOUT_BINDINGS[channel].entity_id].state = "unavailable"
    if power != "unknown":
        entity = physical.ROLLOUT_BINDINGS[1].entity_id
        native[entity].state = power
        session = port.read_state(physical.ROLLOUT_BINDINGS[3].entity_id).observation.session
        physical.feed(port, physical.report(clock, channel=1, session=session, sequence=4, value=power.upper()))
    context = await provider.build_context(hass, config, clock["now"])
    runtime = _make_runtime(hass, payload=config.to_dict(), now_ms=lambda: clock["now"])
    runtime._state_reader = port.read_state
    runtime._proven_power_pair_off = lambda _: power == "off"
    projected = runtime._apply_proven_power_pair(context, config)
    for target in config.devices.light_targets:
        expected = SensorState.ON if target_state == "fresh" else SensorState.UNKNOWN
        if target.id == "light_main" and power == "off" and target_state != "fresh":
            expected = SensorState.OFF
        assert projected.light(target.id).state is expected, (power, target_state, target.id)


@pytest.mark.parametrize("change", ["unknown", "arrival", "manual", "config", "gap", "23"])
async def test_completed_evening_handover_is_volatile_and_scoped(change):
    provider, config, hass, clock, _, _ = replay()
    runtime = _make_runtime(hass, payload=config.to_dict(), now_ms=lambda: clock["now"])
    context = await provider.build_context(hass, config, clock["now"])
    context = replace(context, lights=tuple(
        replace(light, state=SensorState.ON if light.target_id == "light_mirror" else SensorState.OFF)
        for light in context.lights))
    assert runtime._with_evening_handover(context, config).evening_handover_complete
    later = replace(context, now=context.now + 300_001,
                    lights=tuple(replace(light, state=SensorState.UNKNOWN) for light in context.lights),
                    sensors=tuple(replace(sensor, state=SensorState.ON if change == "arrival" else SensorState.UNKNOWN)
                                  for sensor in context.sensors))
    if change == "manual":
        runtime._ownership.record_manual(config.room_id, "light_main", later.now)
    elif change == "config":
        config = replace(config, version=config.version + 1)
    elif change == "gap":
        later = replace(later, unobserved_since=later.now)
    elif change == "23":
        later = replace(later, now=int(datetime(2026, 10, 16, 23, tzinfo=timezone.utc).timestamp() * 1000))
    result = runtime._with_evening_handover(later, config)
    assert result.evening_handover_complete is (change in {"unknown", "arrival"})
    if result.evening_handover_complete:
        assert not evaluate_curve_room(config, result, presence_confirmed=False, absence_seconds=None).commands


async def test_equal_status_reads_preserve_completed_evening_handover():
    from copy import deepcopy
    provider, config, hass, clock, _, _ = replay()
    runtime = _make_runtime(hass, payload=config.to_dict(), now_ms=lambda: clock["now"])
    context = await provider.build_context(hass, config, clock["now"])
    context = replace(context, lights=tuple(
        replace(light, state=SensorState.ON if light.target_id == "light_mirror" else SensorState.OFF)
        for light in context.lights))
    runtime._configs[config.room_id] = config
    assert runtime._with_evening_handover(context, config).evening_handover_complete
    later = replace(context, now=context.now + 300_001,
                    lights=tuple(replace(light, state=SensorState.UNKNOWN) for light in context.lights))

    async def build(candidate):
        return runtime._with_evening_handover(later, candidate)

    runtime._build_context = build
    for _ in range(3):
        result = await runtime.async_context_for(deepcopy(config))
        assert result.evening_handover_complete
        assert not evaluate_curve_room(config, result, presence_confirmed=False, absence_seconds=None).commands
