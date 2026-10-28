"""Agreed 23:00 cutoff, synthetic snapshots only, no household IO."""

import asyncio
from dataclasses import replace
from datetime import datetime, time, timezone

import pytest

from custom_components.hausman_hub.domain.light_curve import (
    LightCurveContext, LightCurveProfile, evaluate_light_curve,
)
from custom_components.hausman_hub.domain.room_lighting import LightRole
from custom_components.hausman_hub.domain.room_lighting_engine import (
    LightAction, evaluate_curve_room,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import (
    ExplicitHandoffProof, OwnershipSnapshot, OwnershipSource, SensorState,
    has_automatic_control, has_proven_auto_ownership,
)
from test_evening_mirror_handover import _configuration, _context, _spots
from test_room_lighting_engine import _at, _auto, _manual, _light
from test_room_lighting_runtime import (
    _FakeHass, _ROOM_ID, _curve_payload, _event, _make_runtime,
)


@pytest.mark.parametrize("at", [time(23), time(23, 30), time(23, 59, 59),
                                  time(0), time(6, 59, 59)])
@pytest.mark.parametrize("presence", list(SensorState))
def test_night_never_requests_on(at, presence):
    curve = evaluate_light_curve(LightCurveProfile(), LightCurveContext(
        now=at, sunrise=time(7), sunset=time(23, 59), presence=presence,
        chandelier=SensorState.ON, mirror=SensorState.ON,
        presence_confirmed=True, motion_preview=True,
        evening_handover_complete=True,
    ))
    assert not curve.hold
    assert not curve.chandelier_on and not curve.mirror_on


@pytest.mark.parametrize("hour,minute", [(23, 0), (23, 37), (0, 0), (6, 59)])
def test_night_switches_every_automatic_light_off(hour, minute):
    now = _at(hour, minute)
    config = _configuration()
    base = config.devices.light_targets[0]
    extra = tuple(replace(base, id=f"extra_{role.value}", role=role,
                          entity_id=f"light.extra_{role.value}") for role in LightRole)
    config = replace(config, devices=replace(config.devices,
                     light_targets=config.devices.light_targets + extra))
    context = replace(_context(now=now, mirror=SensorState.ON),
        lights=tuple(_light(t.id, SensorState.ON, now) for t in config.devices.light_targets),
        ownership=tuple(_auto(t.id, now - 1000) for t in config.devices.light_targets))
    decision = evaluate_curve_room(config, context, presence_confirmed=True,
                                   absence_seconds=None)
    assert {(c.target_id, c.action) for c in decision.commands} == {
        (t.id, LightAction.TURN_OFF) for t in config.devices.light_targets}


@pytest.mark.parametrize("state", [SensorState.UNKNOWN, SensorState.UNAVAILABLE])
def test_unknown_is_not_off_or_permission_to_switch(state):
    now = _at(23)
    context = replace(_context(now=now, mirror=SensorState.ON),
        lights=tuple(_light(t.id, state, now) for t in _configuration().devices.light_targets))
    decision = evaluate_curve_room(_configuration(), context,
                                   presence_confirmed=True, absence_seconds=None)
    assert not decision.commands


@pytest.mark.parametrize("target_id", ["light_main", "light_mirror", "light_spots"])
def test_manual_on_protects_the_whole_interchangeable_room(target_id):
    now = _at(23)
    context = replace(_context(now=now, mirror=SensorState.ON), ownership=(
        *(_auto(t.id, now - 2000) for t in _configuration().devices.light_targets),
        _manual(target_id, now - 1000),
    ))
    assert not evaluate_curve_room(_configuration(), context,
        presence_confirmed=True, absence_seconds=None).commands


@pytest.mark.parametrize("on", [False, True])
def test_no_control_or_gap_never_gives_ownership(on):
    now = _at(23)
    context = _context(now=now, mirror=SensorState.ON if on else SensorState.OFF)
    for change in ({"ownership": ()}, {"unobserved_since": now - 500}):
        assert not evaluate_curve_room(_configuration(), replace(context, **change),
            presence_confirmed=True, absence_seconds=None).commands


@pytest.mark.parametrize("confirmed", [False, True])
def test_explicit_handoff_can_switch_off_without_fabricating_auto_on(confirmed):
    now = _at(23)
    config = _configuration()
    context = replace(_context(now=now, mirror=SensorState.ON), ownership=tuple(
        OwnershipSnapshot(t.id, OwnershipSource.EXPLICIT_HANDOFF, confirmed, now - 1000,
            handoff=ExplicitHandoffProof(t.entity_id, config.version, 1, 0, now - 1000))
        for t in config.devices.light_targets))
    decision = evaluate_curve_room(config, context,
                                   presence_confirmed=False, absence_seconds=None)
    assert all(has_automatic_control(context.ownership, t.id) is confirmed
               for t in config.devices.light_targets)
    assert not any(has_proven_auto_ownership(context.ownership, t.id)
                   for t in config.devices.light_targets)
    if not confirmed:
        assert not decision.commands
        return
    assert len(decision.commands) == len(config.devices.light_targets)
    assert all(c.action is LightAction.TURN_OFF for c in decision.commands)
    mirror_off = next(c for c in decision.commands if c.target_id == "light_mirror")
    assert mirror_off.fade_seconds == 0


@pytest.mark.parametrize("on", [False, True])
def test_opted_out_light_does_not_get_night_commands(on):
    now = _at(23)
    config = _configuration()
    config = replace(config, devices=replace(config.devices, light_targets=tuple(
        replace(t, auto_control=False) if t.id == "light_spots" else t
        for t in config.devices.light_targets)))
    context = _context(now=now, mirror=SensorState.ON)
    if not on:
        context = replace(context, lights=tuple(
            replace(light, state=SensorState.OFF) if light.target_id == "light_spots" else light
            for light in context.lights))
    decision = evaluate_curve_room(config, context,
                                   presence_confirmed=True, absence_seconds=None)
    assert not any(c.target_id == "light_spots" for c in decision.commands)


@pytest.mark.parametrize("mode", ["normal", "manual_on", "manual_off", "shadow"])
async def test_regular_clock_switches_all_off_and_manual_intervention_cancels_rest(mode):
    hass = _FakeHass()
    now = [_at(22, 59)]
    payload = _curve_payload(commands_enabled=mode != "shadow")
    payload["devices"]["light_targets"].append(_spots())
    entities = {"light_main": "light.demo_main", "light_mirror": "light.demo_mirror",
                "light_spots": "light.demo_spots"}

    def state(entity, value):
        hass.states.set(entity, value,
            last_changed=datetime.fromtimestamp(now[0] / 1000, timezone.utc))

    for entity in entities.values():
        state(entity, "on")
    state("binary_sensor.demo_presence", "on")

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute(self, hass, command):
            self.calls.append(command)
            state(entities[command.target_id], "off")
            if mode.startswith("manual_") and len(self.calls) == 1:
                now[0] += 1
                runtime._service_event(_event("light", "turn_" + mode[7:],
                                             entities["light_spots"]))
            return {"confirmed": True}

    executor = Executor()
    runtime = _make_runtime(hass, executor=executor, payload=payload, now_ms=lambda: now[0])
    await runtime.start(hass, "entry")
    try:
        assert not executor.calls
        for target_id in entities:
            runtime._ownership.record_auto(_ROOM_ID, target_id, now[0], confirmed=True)
        now[0] = _at(23)
        # Regular time evaluation, not reload, UI return or sensor edge.
        previous = len(hass.tasks)
        runtime._clock_event(None)
        await asyncio.gather(*hass.tasks[previous:])
        assert all(c.action is LightAction.TURN_OFF for c in executor.calls)
        expected = 3 if mode == "normal" else 0 if mode == "shadow" else 1
        assert len(executor.calls) == expected
        if mode == "normal":
            assert {c.target_id for c in executor.calls} == set(entities)
            executor.calls.clear()
            # Still occupied after midnight: no night ON and no redundant OFF.
            now[0] = _at(0) + 86400_000
            for entity in entities.values():
                state(entity, "off")
            state("binary_sensor.demo_presence", "on")
            await runtime.async_process()
            assert not executor.calls
    finally:
        await runtime.stop()
