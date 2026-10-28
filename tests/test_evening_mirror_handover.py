"""22:00 legacy handover: fixed time, observed replacement, manual priority.

Only synthetic ports and observations are used, never the real home.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.hausman_hub.domain.light_curve import (
    CurveReason, LightCurveContext, LightCurveProfile, evaluate_light_curve,
)
from custom_components.hausman_hub.domain.room_lighting import config_from_payload
from custom_components.hausman_hub.domain.room_lighting_engine import (
    EnginePolicy, LightAction, ProtectionSnapshot, evaluate_curve_room,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState
from test_room_lighting_engine import (
    _at, _auto, _config, _ctx, _latched_protection, _light, _manual,
)
from test_room_lighting_runtime import (
    _FakeHass, _ROOM_ID, _curve_payload, _event, _make_runtime,
)


def _spots():
    return {
        "id": "light_spots", "name": "Точки", "kind": "light",
        "entityId": "light.demo_spots", "role": "accent", "groupId": None,
        "brightness": False, "color_temperature": False, "autoAdoptOverride": None,
    }


def _configuration():
    payload = _config(profile="day_curve").to_dict()
    payload["devices"]["light_targets"].append(_spots())
    return config_from_payload(payload)


def _context(*, mirror=SensorState.OFF, main=SensorState.ON,
             presence=SensorState.OFF, now=None):
    now = now if now is not None else _at(22)
    states = (("light_main", main), ("light_mirror", mirror),
              ("light_spots", SensorState.ON))
    return _ctx(now, presence=presence,
                lights=tuple(_light(name, state, now) for name, state in states),
                ownership=tuple(_auto(name, now - 1000) for name, _ in states))


def _evaluate(context=None, config=None, **kwargs):
    return evaluate_curve_room(
        config if config is not None else _configuration(),
        context if context is not None else _context(),
        presence_confirmed=False, absence_seconds=0, **kwargs,
    )


def _actions(decision):
    return [(command.target_id, command.action) for command in decision.commands]


@pytest.mark.parametrize("sunset", [time(18), time(22, 30), time(23, 59)])
@pytest.mark.parametrize("at", [time(22), time(22, 59, 59)])
def test_fixed_22_window_ignores_sunset(at, sunset):
    context = LightCurveContext(
        now=at, sunrise=time(7), sunset=sunset, presence=SensorState.OFF,
        chandelier=SensorState.ON, mirror=SensorState.OFF, absence_seconds=0,
    )
    decision = evaluate_light_curve(LightCurveProfile(), context)
    assert decision.reason is CurveReason.NIGHT
    assert decision.mirror_on and not decision.chandelier_on


@pytest.mark.parametrize("at,mirror,main", [
    (time(21, 59, 59), False, True), (time(23), False, False),
    (time(0), False, False), (time(7), True, False), (time(12), False, True),
])
def test_boundaries_keep_other_time_modes(at, mirror, main):
    context = LightCurveContext(
        now=at, sunrise=time(7), sunset=time(19), presence=SensorState.OFF,
        chandelier=SensorState.ON, mirror=SensorState.OFF, absence_seconds=0,
    )
    decision = evaluate_light_curve(LightCurveProfile(), context)
    assert (decision.mirror_on, decision.chandelier_on) == (mirror, main)


def test_phase_one_only_turns_mirror_on():
    assert _actions(_evaluate()) == [("light_mirror", LightAction.TURN_ON)]


def test_phase_two_turns_both_automatic_sources_off():
    assert _actions(_evaluate(_context(mirror=SensorState.ON))) == [
        ("light_main", LightAction.TURN_OFF), ("light_spots", LightAction.TURN_OFF),
    ]


@pytest.mark.parametrize("state", [SensorState.UNKNOWN, SensorState.UNAVAILABLE])
def test_missing_mirror_confirmation_never_removes_main_or_spots(state):
    assert not _evaluate(_context(mirror=state)).commands


def test_missing_mirror_target_never_removes_sources():
    payload = _configuration().to_dict()
    payload["devices"]["light_targets"] = [
        item for item in payload["devices"]["light_targets"]
        if item["id"] != "light_mirror"
    ]
    assert not _evaluate(config=config_from_payload(payload)).commands


@pytest.mark.parametrize("source", ["light_main", "light_mirror", "light_spots"])
@pytest.mark.parametrize("state", [SensorState.ON, SensorState.OFF])
def test_manual_action_on_any_source_blocks_whole_profile(source, state):
    context = _context(mirror=SensorState.ON)
    context = replace(
        context,
        lights=tuple(replace(item, state=state) if item.target_id == source else item
                     for item in context.lights),
        ownership=tuple(item for item in context.ownership if item.target_id != source)
                  + (_manual(source, context.now),),
    )
    assert not _evaluate(context).commands


@pytest.mark.parametrize("source", ["light_main", "light_mirror", "light_spots"])
def test_manual_mode_keeps_whole_profile(source):
    payload = _configuration().to_dict()
    for item in payload["devices"]["light_targets"]:
        if item["id"] == source:
            item["autoControl"] = False
    assert not _evaluate(_context(mirror=SensorState.ON),
                         config_from_payload(payload)).commands


@pytest.mark.parametrize("presence", [SensorState.ON, SensorState.UNKNOWN, SensorState.UNAVAILABLE])
def test_presence_or_sensor_fault_before_handover_never_turns_sources_off(presence):
    decision = _evaluate(_context(presence=presence))
    assert not any(action is LightAction.TURN_OFF for _, action in _actions(decision))
    assert not any(target == "light_mirror" and action is LightAction.TURN_ON
                   for target, action in _actions(decision))


def test_stale_absence_does_not_start_handover():
    context = _context()
    context = replace(context, sensors=tuple(
        replace(sensor, last_changed=context.now - (EnginePolicy().staleness_seconds + 1) * 1000)
        for sensor in context.sensors
    ))
    assert not any(action is LightAction.TURN_OFF for _, action in _actions(_evaluate(context)))
    assert not any(target == "light_mirror" for target, _ in _actions(_evaluate(context)))


def test_delayed_absence_at_2240_starts_handover_without_extra_five_minutes():
    assert _actions(_evaluate(_context(now=_at(22, 40)))) == [
        ("light_mirror", LightAction.TURN_ON),
    ]


def test_boundary_uses_room_omsk_time_not_utc():
    context = replace(_context(now=_at(16)), timezone=ZoneInfo("Asia/Omsk"))
    assert _actions(_evaluate(context)) == [("light_mirror", LightAction.TURN_ON)]


def test_arrival_before_phase_two_cancels_offs():
    decision = _evaluate(_context(mirror=SensorState.ON, presence=SensorState.ON))
    assert not any(action is LightAction.TURN_OFF and target != "light_mirror"
                   for target, action in _actions(decision))


@pytest.mark.parametrize("presence", [SensorState.ON, SensorState.UNKNOWN])
def test_completed_handover_keeps_mirror_on_arrival_or_fault(presence):
    assert not _evaluate(_context(main=SensorState.OFF, mirror=SensorState.ON,
                                  presence=presence)).commands


def test_motion_preview_does_not_remove_sources_even_after_main_off():
    context = _context(main=SensorState.OFF, mirror=SensorState.ON)
    assert not _evaluate(context, motion_preview=True).commands


@pytest.mark.parametrize("block", ["away", "gap", "protection"])
def test_away_restart_gap_and_manual_protection_keep_sources(block):
    context = _context(mirror=SensorState.ON)
    if block == "away":
        context = replace(context, away=True)
    elif block == "gap":
        context = replace(context, unobserved_since=context.now)
    else:
        context = replace(context, protection=ProtectionSnapshot(active=True, started_at=context.now))
    assert not _evaluate(context).commands


def test_expired_manual_off_and_absence_latch_alone_do_not_relight_mirror():
    context = _context(main=SensorState.OFF)
    context = replace(
        context, ownership=(_manual("light_main", context.now - 7_200_000),),
        protection=_latched_protection(context.now),
    )
    # Spots must not introduce an unrelated unowned-ON block into this check.
    context = replace(context, lights=tuple(replace(item, state=SensorState.OFF)
                                           for item in context.lights))
    assert not _evaluate(context).commands


@pytest.mark.parametrize("mode", [
    "normal", "unconfirmed", "unobserved_mirror", "arrival", "manual_on", "manual_off",
    "arrival_after_main", "manual_after_main", "shadow", "occupied", "unknown", "stale",
])
async def test_regular_clock_path_and_interleaving(mode):
    hass = _FakeHass()
    now = [_at(21, 59)]
    payload = _curve_payload(commands_enabled=mode != "shadow")
    payload["devices"]["light_targets"].append(_spots())

    def state(entity, value, attributes=None):
        hass.states.set(entity, value, attributes or {},
                        last_changed=datetime.fromtimestamp(now[0] / 1000, timezone.utc))

    state("light.demo_main", "on", {"brightness": 13, "color_temp_kelvin": 3400})
    state("light.demo_spots", "on")
    state("light.demo_mirror", "off")
    state("binary_sensor.demo_presence", "off")

    class Executor:
        def __init__(self):
            self.calls = []

        async def execute(self, hass, command):
            self.calls.append(command)
            if mode == "unconfirmed":
                return {"confirmed": False}
            if mode != "unobserved_mirror":
                state("light.demo_" + {"light_main": "main", "light_mirror": "mirror",
                                      "light_spots": "spots"}[command.target_id],
                      "off" if command.action is LightAction.TURN_OFF else "on")
            if command.target_id == "light_mirror":
                if mode == "arrival":
                    state("binary_sensor.demo_presence", "on")
                if mode in ("manual_on", "manual_off"):
                    now[0] += 1
                    runtime._service_event(_event("light", "turn_" + mode[7:], "light.demo_spots"))
            if command.target_id == "light_main" and command.action is LightAction.TURN_OFF:
                if mode == "arrival_after_main":
                    state("binary_sensor.demo_presence", "on")
                if mode == "manual_after_main":
                    now[0] += 1
                    runtime._service_event(_event("light", "turn_on", "light.demo_spots"))
            return {"confirmed": True}

    executor = Executor()
    runtime = _make_runtime(hass, executor=executor, payload=payload, now_ms=lambda: now[0])
    await runtime.start(hass, "entry")
    try:
        assert not executor.calls
        for target in ("light_main", "light_spots"):
            runtime._ownership.record_auto(_ROOM_ID, target, now[0], confirmed=True)

        async def tick():
            previous = len(hass.tasks)
            runtime._clock_event(None)
            await asyncio.gather(*hass.tasks[previous:])

        now[0] = _at(22)
        state("binary_sensor.demo_presence", {"occupied": "on", "unknown": "unknown"}.get(mode, "off"))
        if mode == "stale":
            hass.states.set("binary_sensor.demo_presence", "off", last_changed=
                            datetime.fromtimestamp((now[0] - 90_000_000) / 1000, timezone.utc))
        await tick()
        if mode in ("shadow", "occupied", "unknown", "stale"):
            assert not executor.calls
        else:
            assert [(c.target_id, c.action) for c in executor.calls] == [
                ("light_mirror", LightAction.TURN_ON),
            ]
        now[0] += 1000
        await tick()
        offs = [(c.target_id, c.action) for c in executor.calls
                if c.action is LightAction.TURN_OFF and c.target_id != "light_mirror"]
        if mode == "normal":
            assert offs == [("light_main", LightAction.TURN_OFF),
                            ("light_spots", LightAction.TURN_OFF)]
        elif mode in ("arrival_after_main", "manual_after_main"):
            assert offs == [("light_main", LightAction.TURN_OFF)]
        else:
            assert not offs
        if mode in ("occupied", "unknown", "stale"):
            # Fresh absence later in the hour, not a UI action or a reload.
            now[0] = _at(22, 40)
            state("binary_sensor.demo_presence", "off")
            # Refresh unchanged lamps too: stale lamp evidence must not be
            # smuggled in as a new observed replacement.
            state("light.demo_main", "on", {"brightness": 13, "color_temp_kelvin": 3400})
            state("light.demo_spots", "on")
            state("light.demo_mirror", "off")
            await tick()
            assert [(c.target_id, c.action) for c in executor.calls] == [
                ("light_mirror", LightAction.TURN_ON),
            ]
            now[0] += 1000
            await tick()
            assert [(c.target_id, c.action) for c in executor.calls][-2:] == [
                ("light_main", LightAction.TURN_OFF), ("light_spots", LightAction.TURN_OFF),
            ]
    finally:
        await runtime.stop()
