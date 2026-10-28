"""Bounded command confirmation without devices, network or wall-clock waits."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from dataclasses import replace

import pytest

from custom_components.hausman_hub.application import room_lighting_ha_executor as module
from custom_components.hausman_hub.domain.room_lighting import LightKind, LightTarget
from custom_components.hausman_hub.domain.room_lighting_engine import LightAction, PlannedCommand


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("path", ["light", "auxiliary", "away"])
async def test_room_scoped_executor_keeps_local_ids(monkeypatch, reverse, path):
    from custom_components.hausman_hub.domain.room_lighting import AuxiliaryTarget, AuxiliaryKind
    from custom_components.hausman_hub.domain.room_lighting_engine import DecisionReason

    first = target()
    second = replace(first, entity_id="light.other")
    if path == "auxiliary":
        second = AuxiliaryTarget(id="main", name="Fan", kind=AuxiliaryKind.FAN, entity_id="switch.other_fan")
    rooms = {"first": {"main": first}, "second": {"main": second}}
    if reverse:
        rooms = dict(reversed(tuple(rooms.items())))
    _, own, cache, calls, hass, executor, _ = harness(monkeypatch, window=0)
    executor.update_targets({"main": second}, room_targets=rooms)
    original = hass.services.async_call

    async def service(domain, action, data, **kwargs):
        await original(domain, action, data, **kwargs)
        cache[data["entity_id"]] = state("off" if action == "turn_off" else "on", context=kwargs["context"],
            attributes={"brightness": 255, "color_temp_kelvin": 4000})
    hass.services.async_call = service
    action = LightAction.TURN_OFF if path == "away" else LightAction.TURN_ON
    planned = PlannedCommand("main", action, reason=DecisionReason.AWAY) if path == "away" else PlannedCommand("main", action)
    for room, entity in (("first", "light.main"), ("second", second.entity_id)):
        if path == "away":
            receipt = await executor.execute_away_off(hass, planned, room_id=room, is_current=lambda: True)
        else:
            receipt = await executor.execute(hass, planned, room_id=room)
        assert calls[-1][2]["entity_id"] == entity
        assert receipt.target_id == "main" and receipt.sent and receipt.confirmed
        assert executor.owns_service_context(own, entity, "off" if path == "away" else "on")


@pytest.mark.parametrize("room,local", [("missing", "main"), ("first", "missing")])
async def test_room_scoped_executor_unknown_never_uses_flat_fallback(monkeypatch, room, local):
    _, _, _, calls, hass, executor, _ = harness(monkeypatch, window=0)
    executor.update_targets({"main": target()}, room_targets={"first": {"main": target()}})
    receipt = await executor.execute(hass, PlannedCommand(local, LightAction.TURN_ON), room_id=room)
    assert calls == []
    assert (receipt.target_id, receipt.sent, receipt.send_outcome, receipt.confirmed) == (local, False, "not_sent", False)
    legacy = await executor.execute(hass, command())
    assert legacy.sent and len(calls) == 1  # Direct compatibility caller remains flat.


async def test_room_scoped_executor_registry_replacement_does_not_redirect_pending(monkeypatch):
    _, _, cache, calls, hass, executor, _ = harness(monkeypatch, window=0)
    entered, release = asyncio.Event(), asyncio.Event()
    second = replace(target(), entity_id="light.other")
    executor.update_targets({"main": second}, room_targets={"first": {"main": target()}, "second": {"main": second}})
    original = hass.services.async_call

    async def service(domain, action, data, **kwargs):
        await original(domain, action, data, **kwargs)
        if data["entity_id"] == "light.main":
            entered.set()
            await release.wait()
        cache[data["entity_id"]] = state("on", context=kwargs["context"],
            attributes={"brightness": 255, "color_temp_kelvin": 4000})
    hass.services.async_call = service
    task = asyncio.create_task(executor.execute(hass, command(), room_id="first"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        other = await executor.execute(hass, command(), room_id="second")
        executor.update_targets({}, room_targets={"first": {"main": replace(target(), entity_id="light.replacement")}})
        release.set()
        first = await asyncio.wait_for(task, 2)
        assert [call[2]["entity_id"] for call in calls] == ["light.main", "light.other"]
        assert first.sent and first.confirmed and other.sent and other.confirmed
        assert first.target_id == other.target_id == "main"
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


BASE_MS = 1_792_054_800_000


def state(value, *, context=None, at=BASE_MS, attributes=None):
    stamp = datetime.fromtimestamp(at / 1000, timezone.utc)
    return SimpleNamespace(state=value, context=context, last_reported=stamp,
                           last_updated=stamp, attributes=dict(attributes or {}))


def target():
    return LightTarget(id="main", name="Test", kind=LightKind.LIGHT,
                       brightness=True, color_temperature=True,
                       entity_id="light.main")


def command(action=LightAction.TURN_ON):
    return PlannedCommand("main", action, brightness=100, color_temperature=4000)


class Clock:
    def __init__(self):
        self.seconds = 0.0
        self.sleeps = []
        self.on_sleep = lambda: None

    def now_ms(self):
        return BASE_MS + round(self.seconds * 1000)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.seconds += seconds
        self.on_sleep()


def harness(monkeypatch, *, window=1.0, authority=None):
    clock = Clock()
    context = SimpleNamespace(id="own", parent_id=None, user_id=None)
    monkeypatch.setattr(module, "_new_service_context", lambda: context)
    cache = {"light.main": state("off", at=BASE_MS - 1000)}
    calls = []

    async def service(domain, action, data, **kwargs):
        calls.append((domain, action, data, kwargs))

    hass = SimpleNamespace(states=SimpleNamespace(get=cache.get),
                           services=SimpleNamespace(async_call=service))
    executor = module.RoomLightingHaExecutor(
        {"main": target()}, manual_light_authority=authority,
        light_readback_window_seconds=window,
        light_readback_interval_seconds=0.25,
        now_ms=clock.now_ms, monotonic=lambda: clock.seconds,
        sleep=clock.sleep,
    )

    def report(**kwargs):
        cache["light.main"] = state(
            kwargs.pop("value", "on"), context=kwargs.pop("context", context),
            at=kwargs.pop("at", clock.now_ms()),
            attributes=kwargs.pop("attributes", {"brightness": 255, "color_temp_kelvin": 4000}),
        )
        assert not kwargs

    return clock, context, cache, calls, hass, executor, report


async def test_delayed_report_reproduces_live_command_race(monkeypatch):
    """Fails on the installed implementation: async_call returns before MQTT."""
    context = SimpleNamespace(id="delayed-own", parent_id=None, user_id=None)
    monkeypatch.setattr(module, "_new_service_context", lambda: context)
    cache = {"light.main": state("off", at=BASE_MS - 1000)}
    calls = []
    handles = []

    def report():
        cache["light.main"] = state("on", context=context,
                                    attributes={"brightness": 255, "color_temp_kelvin": 4000})

    async def service(*args, **kwargs):
        calls.append((args, kwargs))
        handles.append(asyncio.get_running_loop().call_later(0.01, report))

    hass = SimpleNamespace(states=SimpleNamespace(get=cache.get),
                           services=SimpleNamespace(async_call=service))
    executor = module.RoomLightingHaExecutor({"main": target()}, now_ms=lambda: BASE_MS)
    try:
        receipt = await executor.execute(hass, command())
        assert receipt.confirmed is True
        assert len(calls) == 1
    finally:
        for handle in handles:
            handle.cancel()


async def test_delayed_own_reply_confirms_all_properties_once(monkeypatch):
    clock, _, _, calls, hass, executor, report = harness(monkeypatch)
    clock.on_sleep = report
    receipt = await executor.execute(hass, command())
    assert receipt.confirmed and receipt.sent
    assert receipt.state_after == {"state": "on", "brightness": 100, "color_temperature": 4000}
    assert clock.sleeps == [0.25]
    assert len(calls) == 1


@pytest.mark.parametrize("bad", ["stale", "future", "foreign", "missing_context", "user",
                                      "restored", "cached", "assumed_state", "optimistic",
                                      "unknown", "unavailable", "wrong_brightness", "wrong_colour"])
async def test_untrusted_or_mismatched_reply_does_not_confirm(monkeypatch, bad):
    clock, own, _, calls, hass, executor, report = harness(monkeypatch)

    def bad_reply():
        args = {}
        attrs = {"brightness": 255, "color_temp_kelvin": 4000}
        if bad == "stale":
            args["at"] = BASE_MS - 1
        elif bad == "future":
            args["at"] = clock.now_ms() + 1000
        elif bad == "foreign":
            args["context"] = SimpleNamespace(id="foreign", parent_id=None)
        elif bad == "missing_context":
            args["context"] = None
        elif bad == "user":
            args["context"] = SimpleNamespace(id=own.id, parent_id=None, user_id="human")
        elif bad in {"restored", "cached", "assumed_state", "optimistic"}:
            attrs[bad] = True
        elif bad in {"unknown", "unavailable"}:
            args["value"] = bad
        elif bad == "wrong_brightness":
            attrs["brightness"] = 100
        elif bad == "wrong_colour":
            attrs["color_temp_kelvin"] = 5000
        report(attributes=attrs, **args)

    clock.on_sleep = bad_reply
    receipt = await executor.execute(hass, command())
    assert receipt.sent and not receipt.confirmed
    assert clock.seconds == 1.0
    assert len(calls) == 1


async def test_old_matching_on_does_not_claim_ownership(monkeypatch):
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch)
    report(at=BASE_MS - 1)
    before = cache["light.main"]
    receipt = await executor.execute(hass, command())
    assert not receipt.confirmed and cache["light.main"] is before
    assert clock.seconds == 1.0 and len(calls) == 1


async def test_new_manual_hold_during_wait_prevents_confirmation(monkeypatch):
    blocked = [False]
    authority = SimpleNamespace(blocks=lambda entity: blocked[0])
    clock, _, _, calls, hass, executor, report = harness(monkeypatch, authority=authority)

    def manual_then_own_reply():
        blocked[0] = True
        report()

    clock.on_sleep = manual_then_own_reply
    receipt = await executor.execute(hass, command())
    assert not receipt.confirmed and receipt.sent
    assert clock.seconds == 0.25 and len(calls) == 1


async def test_partial_parameters_wait_for_complete_reply(monkeypatch):
    clock, _, _, calls, hass, executor, report = harness(monkeypatch)

    def reply():
        attrs = {"brightness": 255, "color_temp_kelvin": 5000 if clock.seconds < 0.5 else 4000}
        report(attributes=attrs)

    clock.on_sleep = reply
    receipt = await executor.execute(hass, command())
    assert receipt.confirmed and clock.seconds == 0.5 and len(calls) == 1


@pytest.mark.parametrize("value, expected", [("off", True), ("unknown", False), ("unavailable", False), ("on", False)])
async def test_off_requires_explicit_off_reply(monkeypatch, value, expected):
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch)
    cache["light.main"] = state("on", at=BASE_MS - 1000)
    clock.on_sleep = lambda: report(value=value, attributes={})
    receipt = await executor.execute(hass, command(LightAction.TURN_OFF))
    assert receipt.confirmed is expected and len(calls) == 1


async def test_cancellation_stops_wait_without_second_command(monkeypatch):
    clock, _, _, calls, hass, executor, _ = harness(monkeypatch)

    async def cancelled(seconds):
        raise asyncio.CancelledError()

    executor._sleep = cancelled
    with pytest.raises(asyncio.CancelledError):
        await executor.execute(hass, command())
    assert len(calls) == 1
    assert not executor._active_context_ids


async def test_timeout_is_bounded_and_late_reply_does_not_change_receipt(monkeypatch):
    clock, _, _, calls, hass, executor, report = harness(monkeypatch, window=0.6)
    receipt = await executor.execute(hass, command())
    assert not receipt.confirmed and receipt.sent
    assert clock.seconds == 0.6
    report()
    assert not receipt.confirmed and len(calls) == 1


async def test_child_of_exact_command_context_is_accepted(monkeypatch):
    clock, own, _, _, hass, executor, report = harness(monkeypatch)
    clock.on_sleep = lambda: report(context=SimpleNamespace(id="child", parent_id=own.id, user_id=None))
    assert (await executor.execute(hass, command())).confirmed


@pytest.mark.parametrize("physical", ["fresh", "absent", "stale", "conflicting"])
async def test_configured_physical_reader_cannot_be_bypassed_by_native_cache(monkeypatch, physical):
    from custom_components.hausman_hub.application.lighting_observation import (
        ObservedLightState, PhysicalLightObservation,
    )
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch)
    observation = [None]

    def reader(entity):
        native = cache.get(entity)
        native.entity_id = entity
        return ObservedLightState(native, observation[0])

    executor._state_reader = reader

    def reply():
        report()
        if physical != "absent":
            observation[0] = PhysicalLightObservation(
                "light.main", "off" if physical == "conflicting" else "on",
                BASE_MS - 1 if physical == "stale" else clock.now_ms(),
                "session", 1, "0x0000000000000001", 1,
            )

    clock.on_sleep = reply
    receipt = await executor.execute(hass, command())
    assert receipt.confirmed is (physical == "fresh")
    assert len(calls) == 1


@pytest.mark.parametrize("argument", ["light_readback_window_seconds", "light_readback_interval_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_readback_time_parameters_must_be_finite(argument, value):
    with pytest.raises(ValueError):
        module.RoomLightingHaExecutor(**{argument: value})


@pytest.mark.parametrize("action", [LightAction.TURN_ON, LightAction.SET_COLOR_TEMPERATURE])
async def test_old_same_millisecond_physical_frame_cannot_confirm_new_on(monkeypatch, action):
    from custom_components.hausman_hub.application.lighting_observation import (
        ObservedLightState, PhysicalLightObservation, PhysicalColorObservation,
    )
    clock, _, cache, calls, hass, executor, report = harness(monkeypatch)
    # Old physical ON and the new send share millisecond resolution. A new
    # wrapper/native context alone must not make that frame a new ON proof.
    old = PhysicalLightObservation("light.main", "on", BASE_MS, "session", 7,
                                   "0x0000000000000001", 1)
    cache["light.main"] = state("on", at=BASE_MS - 1000)

    def reader(entity):
        native = cache[entity]
        native.entity_id = entity
        color = None
        if action is LightAction.SET_COLOR_TEMPERATURE and native.context is not None:
            color = PhysicalColorObservation(entity, 250, clock.now_ms(), "session", 8,
                                             "0x0000000000000001", 1)
        return ObservedLightState(native, old, None, color)

    executor._state_reader = reader
    clock.on_sleep = report
    receipt = await executor.execute(hass, command(action))
    # New physical CCT plus the new causal native reply confirms colour.
    # The old physical ON can never earn new ON ownership.
    assert receipt.confirmed is (action is LightAction.SET_COLOR_TEMPERATURE)
    assert len(calls) == 1


async def test_infinite_wait_cannot_be_requested_with_large_finite_window(monkeypatch):
    clock, _, _, calls, hass, executor, _ = harness(monkeypatch, window=1e100)
    assert not (await executor.execute(hass, command())).confirmed
    assert clock.seconds == module.MAX_LIGHT_READBACK_WINDOW_SECONDS
    assert len(calls) == 1


@pytest.mark.parametrize("manual", [None, "same_on", "same_off", "alternate_on"])
@pytest.mark.parametrize("profile", ["regular", "day_curve"])
async def test_runtime_delayed_receipt_grants_auto_only_without_manual_intervention(monkeypatch, manual, profile):
    from tests.test_room_lighting_runtime import (
        _FakeHass, _NOW_MS, _ROOM_ID, _make_runtime, _seed_presence_and_light,
        _config_payload, _curve_payload,
    )
    from custom_components.hausman_hub.domain.room_lighting_ownership import has_proven_auto_ownership

    clock = Clock()
    clock.now_ms = lambda: _NOW_MS + round(clock.seconds * 1000)
    context = SimpleNamespace(id="delayed-runtime", parent_id=None, user_id=None)
    monkeypatch.setattr(module, "_new_service_context", lambda: context)
    hass = _FakeHass()
    _seed_presence_and_light(hass)
    hass.states.set("binary_sensor.demo_presence", "on",
                    last_changed=datetime.fromtimestamp((_NOW_MS - 20_000) / 1000, timezone.utc))
    hass.states.values["light.demo_mirror"] = state("off", at=_NOW_MS)
    sent = []
    executor = module.RoomLightingHaExecutor(
        now_ms=clock.now_ms, monotonic=lambda: clock.seconds, sleep=clock.sleep,
        light_readback_window_seconds=1.0, light_readback_interval_seconds=0.25,
    )
    payload = _config_payload(commands_enabled=True)
    payload["devices"]["light_targets"] = _curve_payload(commands_enabled=True)["devices"]["light_targets"]
    if profile == "day_curve":
        payload["profile"] = "day_curve"
    runtime = _make_runtime(hass, executor=executor, payload=payload, now_ms=clock.now_ms)

    async def service(domain, action, data, **kwargs):
        sent.append((domain, action, dict(data), kwargs["context"]))
        own_event = SimpleNamespace(data={"domain": domain, "service": action,
                                          "service_data": data}, context=kwargs["context"])
        runtime._service_event(own_event)

    hass.services = SimpleNamespace(async_call=service)

    def delayed_reply():
        domain, action, data, own = sent[-1]
        if manual:
            manual_service = "turn_off" if manual == "same_off" else "turn_on"
            manual_entity = "light.demo_mirror" if manual == "alternate_on" else data["entity_id"]
            runtime._service_event(SimpleNamespace(
                data={"domain": "light", "service": manual_service,
                      "service_data": {"entity_id": manual_entity}},
                context=SimpleNamespace(id="human", parent_id=None, user_id="human"),
            ))
        attrs = {}
        if "brightness_pct" in data:
            attrs["brightness"] = round(data["brightness_pct"] * 255 / 100)
        if "color_temp_kelvin" in data:
            attrs["color_temp_kelvin"] = data["color_temp_kelvin"]
        hass.states.values[data["entity_id"]] = state(
            "on" if action == "turn_on" else "off", context=own,
            at=clock.now_ms(), attributes=attrs,
        )

    clock.on_sleep = delayed_reply
    try:
        await runtime.start(hass, "entry")
        records = runtime._ownership.snapshots_for(_ROOM_ID, {"light_main"})
        assert len(sent) == 1
        assert has_proven_auto_ownership(records, "light_main") is (manual is None)
        if manual is None:
            assert clock.seconds == 0.25
        else:
            # The delayed OWN reply cannot replace the newer manual boundary,
            # including an action at exactly the same timestamp.
            assert not has_proven_auto_ownership(records, "light_main")
        await runtime.async_process((_ROOM_ID,))
        assert len(sent) == 1
    finally:
        await runtime.stop()
