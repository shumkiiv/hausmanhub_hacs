"""Direct device adjustments use the same authority as service gestures."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_authority import ManualLightAuthority
from custom_components.hausman_hub.application.scenario_command_context import ScenarioCommandContextRegistry
from custom_components.hausman_hub.manual_light_authority_events import ManualLightAuthorityEvents, _parameter_action


@pytest.mark.parametrize("action,duration", [("on", 1234000), ("off", 321000)])
async def test_capture_uses_room_duration_for_each_direction(action, duration):
    from custom_components.hausman_hub.manual_light_authority_events import _StartupEvidence
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        SimpleNamespace(async_create_task=asyncio.create_task), authority,
        lambda: {"light.demo"}, None, lambda: None,
        priority_duration=lambda entity, value: duration if (entity, value) == ("light.demo", action) else 0,
    )
    evidence = _StartupEvidence("light.demo", action, "service", "gesture", "event", 100000)
    listener._capture_evidence(evidence, schedule_drain=False)
    record = authority.record("light.demo")
    assert record.deadline_ms == 100000 + duration
    listener._capture_evidence(evidence, schedule_drain=False)
    assert authority.record("light.demo") is record


def test_changed_duration_recomputes_from_gesture_without_rearming():
    import time
    authority = ManualLightAuthority()
    now = int(time.time()*1000)
    authority.capture("light.demo", "on", "service", "gesture", observed_at_ms=now-20000, duration_ms=15000)
    assert not authority.blocks("light.demo")
    authority.set_priority_duration_resolver(lambda *_: 30000)
    assert authority.blocks("light.demo")
    record = authority.record("light.demo")
    authority.set_priority_duration_resolver(lambda *_: 15000)
    assert not authority.blocks("light.demo")
    assert authority.record("light.demo") is record


def state(attrs, observed=None):
    return SimpleNamespace(state="on", attributes=attrs,
        last_changed=datetime.now(timezone.utc)-timedelta(days=1),
        last_updated=observed or datetime.now(timezone.utc))


async def deliver(old_attrs, new_attrs, *, observed=None, own=None, registry=None, entity="light.demo"):
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(SimpleNamespace(async_create_task=asyncio.create_task),
        authority, lambda: {"light.demo"}, registry, lambda: None)
    event = SimpleNamespace(data={"entity_id": entity, "old_state": state(old_attrs),
        "new_state": state(new_attrs, observed)}, context=own or SimpleNamespace(id="physical"))
    listener.state_event(event)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return authority, listener, event


@pytest.mark.parametrize("new", [{"brightness": 51}, {"color_temp_kelvin": 5000}])
async def test_direct_parameter_change_with_old_transition_has_manual_priority(new):
    original = {"brightness": 153, "color_temp_kelvin": 4000}
    authority, listener, event = await deliver(original, dict(original, **new))
    record = authority.record("light.demo")
    assert record is not None and record.action == "on"
    assert authority.blocks("light.demo")
    listener.state_event(event)
    assert authority.record("light.demo") == record
    event.data["new_state"].attributes["brightness"] = 64
    listener.state_event(event)
    assert authority.record("light.demo").generation > record.generation
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.parametrize("attrs", [{"cached": True}, {"restored": True}, {"assumed_state": True}])
async def test_untrusted_parameter_report_never_proves_manual_action(attrs):
    authority, _, _ = await deliver({"brightness": 153}, dict(attrs, brightness=51))
    assert authority.record("light.demo") is None


@pytest.mark.parametrize("offset", [-301, 1])
async def test_stale_or_future_parameter_report_is_not_a_gesture(offset):
    authority, _, _ = await deliver({"brightness": 153}, {"brightness": 51},
        observed=datetime.now(timezone.utc)+timedelta(seconds=offset))
    assert authority.record("light.demo") is None


async def test_exact_own_parameter_report_is_ignored_but_expired_or_wrong_scope_is_manual():
    clock = [100.0]
    own = SimpleNamespace(id="automatic", parent_id=None)
    registry = ScenarioCommandContextRegistry(monotonic=lambda: clock[0], context_factory=lambda: own)
    registry.create("light.demo", "on")
    authority, listener, event = await deliver({"brightness": 153}, {"brightness": 51}, own=own, registry=registry)
    assert authority.record("light.demo") is None
    clock[0] = 130.0
    listener.state_event(event)
    assert authority.record("light.demo") is not None
    for entity, action in [("light.other", "on"), ("light.demo", "off")]:
        registry = ScenarioCommandContextRegistry(context_factory=lambda: own)
        registry.create(entity, action)
        authority, _, _ = await deliver({"brightness": 153}, {"brightness": 51}, own=own, registry=registry)
        assert authority.record("light.demo") is not None


@pytest.mark.parametrize("old,new,expected", [
    ({"brightness": 153}, {"brightness": 153, "diagnostic": "new"}, None),
    ({"color_temp_kelvin": 4000}, {"color_temp": 250}, None),
    ({"color_mode": "hs", "hs_color": (10, 50), "rgb_color": (1, 2, 3)},
     {"color_mode": "hs", "hs_color": [10, 50], "rgb_color": (4, 5, 6)}, None),
    ({"color_mode": "hs", "hs_color": (10, 50)}, {"color_mode": "rgb", "rgb_color": (1, 2, 3)}, "on"),
    ({"brightness": 153}, {}, "unknown"),
    ({"color_temp_kelvin": 4000}, {"color_mode": {}}, "unknown"),
    ({}, {"brightness": 153}, None),
])
def test_only_complete_meaningful_active_control_changes_are_gestures(old, new, expected):
    assert _parameter_action(state(old), state(new)) == expected
