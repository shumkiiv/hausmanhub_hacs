from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
    valid_manual_light_authority_payload,
)
from custom_components.hausman_hub.manual_light_authority_events import (
    ManualLightAuthorityEvents,
)
from custom_components.hausman_hub.application.room_lighting_ha_executor import (
    RoomLightingHaExecutor,
)
from custom_components.hausman_hub.domain.room_lighting import LightKind, LightTarget
from custom_components.hausman_hub.domain.room_lighting_engine import (
    DecisionReason,
    LightAction,
    PlannedCommand,
)


class _Store:
    recovered_previous = False
    def __init__(self) -> None: self.payload = None
    async def async_load(self): return self.payload
    async def async_save(self, payload): self.payload = payload


async def test_capture_is_durable_and_duplicate_is_not_extended() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    assert await authority.async_capture("light.demo", "off", "yandex", "ctx-1")
    assert authority.blocks("light.demo")
    assert not await authority.async_capture("light.demo", "off", "yandex", "ctx-1")
    assert store.payload["records"][0]["generation"] == 1


async def test_storage_failure_remains_deny_only() -> None:
    class _FailingStore(_Store):
        async def async_save(self, payload): raise RuntimeError("disk")
    authority = ManualLightAuthority(_FailingStore())
    with pytest.raises(RuntimeError):
        await authority.async_capture("light.demo", "on", "manual", "ctx-2")
    assert authority.blocks("light.other")


async def test_capture_latches_before_first_await_and_has_hour_deadline() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    coroutine = authority.async_capture(
        "light.demo", "on", "service", "ctx-3", observed_at_ms=1_000
    )
    assert not authority.blocks("light.demo")
    # Running the coroutine reaches its first await only after the synchronous
    # latch has already become visible to both dispatch paths.
    task = asyncio.create_task(coroutine)
    await asyncio.sleep(0)
    assert authority.blocks("light.demo")
    await task
    record = authority.record("light.demo")
    assert record is not None and record.deadline_ms == 3_601_000


async def test_event_during_save_drains_new_generation() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.writes = []

        async def async_save(self, payload):
            self.writes.append(payload)
            if len(self.writes) == 1:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    assert authority.capture("light.a", "on", "service", "a", observed_at_ms=1)
    draining = asyncio.create_task(authority.async_drain())
    await store.started.wait()
    assert authority.capture("light.b", "off", "state", "b", observed_at_ms=2)
    assert authority.blocks("light.b")
    store.release.set()
    await draining
    assert len(store.writes) == 2
    assert {r["entityId"] for r in store.writes[0]["records"]} == {"light.a"}
    assert {r["entityId"] for r in store.payload["records"]} == {"light.a", "light.b"}
    assert authority.generation == 2


async def test_restart_recovery_and_partial_legacy_migration_fail_closed() -> None:
    store = _Store()
    original = ManualLightAuthority(store)
    original.capture_legacy("light.old", source="room")
    await original.async_drain()
    restored = ManualLightAuthority(store)
    assert restored.blocks("light.any")  # startup barrier
    await restored.async_load()
    assert restored.blocks("light.old")
    assert restored.blocks("light.other")  # crash gap is unknown on restart
    assert restored.record("light.old").legacy
    assert restored.capture_legacy("light.other", source="scenario")
    await restored.async_drain()
    assert {r["entityId"] for r in store.payload["records"]} == {
        "light.old", "light.other"
    }
    store.recovered_previous = True
    recovered = ManualLightAuthority(store)
    await recovered.async_load()
    assert recovered.blocks("light.unrelated")


async def test_old_duplicate_after_new_action_does_not_extend_deadline() -> None:
    authority = ManualLightAuthority(_Store())
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "first", observed_at_ms=10)
    authority.capture("light.demo", "off", "service", "second", observed_at_ms=20)
    assert not authority.capture(
        "light.demo", "on", "service", "first", observed_at_ms=30
    )
    assert authority.record("light.demo").deadline_ms == 3_600_020


async def test_invalid_persisted_record_fails_closed() -> None:
    store = _Store()
    store.payload = {"version": 2, "records": [{"entityId": "light.demo"}]}
    authority = ManualLightAuthority(store)
    with pytest.raises(ValueError):
        await authority.async_load()
    assert authority.blocks("light.other")
    assert not valid_manual_light_authority_payload(store.payload)


async def test_service_state_dedup_and_new_same_state_service_on() -> None:
    class _Hass:
        def __init__(self):
            self.tasks = []

        def async_create_task(self, coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task

    hass = _Hass()
    authority = ManualLightAuthority(_Store())
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: {"light.demo"}, None, lambda: None
    )
    now = datetime.now(timezone.utc)
    context = SimpleNamespace(id="context-1")
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"entity_id": "light.demo"}},
        context=context, time_fired=now,
    ))
    first_deadline = authority.record("light.demo").deadline_ms
    old = SimpleNamespace(state="off")
    new = SimpleNamespace(state="on", last_changed=now, attributes={})
    listener.state_event(SimpleNamespace(
        data={"entity_id": "light.demo", "old_state": old, "new_state": new},
        context=context,
    ))
    assert authority.generation == 1
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"entity_id": "light.demo"}},
        context=SimpleNamespace(id="context-2"), time_fired=now,
    ))
    assert authority.generation == 2
    assert authority.record("light.demo").deadline_ms >= first_deadline
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_off",
              "service_data": {"entity_id": "light.demo"}},
        context=SimpleNamespace(id="context-3"), time_fired=now,
    ))
    assert authority.record("light.demo").action == "off"
    await asyncio.gather(*hass.tasks)


async def test_room_executor_rechecks_before_physical_call(monkeypatch) -> None:
    from custom_components.hausman_hub.application import room_lighting_ha_executor

    monkeypatch.setattr(
        room_lighting_ha_executor,
        "_new_service_context",
        lambda: SimpleNamespace(id="automatic-1"),
    )
    called = []

    class _Services:
        async def async_call(self, *args, **kwargs):
            called.append((args, kwargs))

    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity: None),
        services=_Services(),
    )
    authority = ManualLightAuthority()
    target = LightTarget(
        id="lamp", name="Свет", kind=LightKind.LIGHT,
        brightness=False, color_temperature=False, entity_id="light.demo",
    )
    executor = RoomLightingHaExecutor(
        {"lamp": target}, manual_light_authority=authority
    )
    command = PlannedCommand(
        "lamp", LightAction.TURN_ON, reason=DecisionReason.SCHEDULE
    )
    authority.capture("light.demo", "off", "service", "ctx-1")
    receipt = await executor.execute(hass, command)
    assert not receipt.sent and not called
    # A physical binding press is a separate manual route; the common deny
    # applies to automatic sends only.
    await executor.execute_manual(hass, command)
    assert len(called) == 1


async def test_room_upstream_power_checks_dependent_light_before_call(monkeypatch) -> None:
    from custom_components.hausman_hub.application import room_lighting_ha_executor

    monkeypatch.setattr(
        room_lighting_ha_executor,
        "_new_service_context",
        lambda: SimpleNamespace(id="automatic-power-1"),
    )
    called = []

    class _Services:
        async def async_call(self, *args, **kwargs):
            called.append((args, kwargs))

    state = SimpleNamespace(state="off", attributes={})
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity: state),
        services=_Services(),
    )
    authority = ManualLightAuthority()
    authority.capture("light.demo", "off", "service", "manual-power")
    executor = RoomLightingHaExecutor(manual_light_authority=authority)
    receipt = await executor.async_power_on(
        hass, "switch.power", protected_entities=("light.demo",)
    )
    assert receipt["reason"] == "manual_light_priority"
    assert receipt["sent"] is False
    assert not called


async def test_room_manual_only_target_refuses_automatic_command(monkeypatch) -> None:
    from custom_components.hausman_hub.application import room_lighting_ha_executor

    monkeypatch.setattr(
        room_lighting_ha_executor,
        "_new_service_context",
        lambda: SimpleNamespace(id="manual-only-context"),
    )
    called = []

    class _Services:
        async def async_call(self, *args, **kwargs):
            called.append((args, kwargs))

    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _entity: None), services=_Services()
    )
    target = LightTarget(
        id="manual_lamp", name="Ручной свет", kind=LightKind.SWITCH,
        brightness=False, color_temperature=False,
        entity_id="switch.manual_lamp", auto_control=False,
    )
    executor = RoomLightingHaExecutor({"manual_lamp": target})
    command = PlannedCommand(
        "manual_lamp", LightAction.TURN_ON, reason=DecisionReason.SCHEDULE
    )
    receipt = await executor.execute(hass, command)
    assert not receipt.sent and not called


async def test_switch_light_event_and_selector_uncertainty() -> None:
    class _Hass:
        def __init__(self):
            self.tasks = []

        def async_create_task(self, coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)

    hass = _Hass()
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: {"switch.lamp"}, None, lambda: None
    )
    listener.service_event(SimpleNamespace(
        data={"domain": "switch", "service": "turn_off",
              "service_data": {"entity_id": "switch.lamp"}},
        context=SimpleNamespace(id="switch-off-1"),
        time_fired=datetime.now(timezone.utc),
    ))
    assert authority.record("switch.lamp").action == "off"
    # An unrelated light area selector cannot include a managed switch.
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"area_id": "unrelated"}},
    ))
    assert not authority.unhealthy
    # Without trustworthy area expansion, a switch selector might include it.
    listener.service_event(SimpleNamespace(
        data={"domain": "switch", "service": "turn_on",
              "service_data": {"area_id": "unknown"}},
    ))
    assert authority.unhealthy
    await asyncio.gather(*hass.tasks)


def test_event_subscription_registers_both_sync_boundaries() -> None:
    callbacks = {}
    cancelled = []

    class _Bus:
        def async_listen(self, event_type, callback):
            callbacks[event_type] = callback
            return lambda: cancelled.append(event_type)

    hass = SimpleNamespace(bus=_Bus())
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: {"light.demo"}, None, lambda: None
    )
    cancel = listener.start()
    assert set(callbacks) == {"call_service", "state_changed"}
    cancel()
    assert set(cancelled) == {"call_service", "state_changed"}
