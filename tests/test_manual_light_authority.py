from __future__ import annotations

import ast
import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.hausman_hub.application.manual_light_authority import (
    ManualLightAuthority,
    ManualLightReturnToken,
    valid_manual_light_authority_payload,
)
from custom_components.hausman_hub.application.related_light_sources import RelatedLightSources
from custom_components.hausman_hub.application.manual_light_auto_return import (
    ManualLightAutoReturn,
)
from custom_components.hausman_hub.manual_light_authority_events import (
    ManualLightAuthorityEvents,
    _StartupEvidence,
    room_light_entity_ids,
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


def test_related_light_sources_extend_deny_without_copying_record() -> None:
    config = SimpleNamespace(room_id="room", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id="light.primary"),
        SimpleNamespace(entity_id="switch.alternate"),
    )))
    graph = RelatedLightSources.from_configs((config,))
    authority = ManualLightAuthority()
    authority.set_related_sources(graph)
    authority.capture("light.primary", "on", "service", "related", observed_at_ms=1)
    assert authority.blocks("switch.alternate")
    assert authority.record("switch.alternate") is None
    assert authority.blocking_evidence("switch.alternate").entity_id == "light.primary"


def test_related_light_sources_fail_closed_when_target_has_no_entity() -> None:
    config = SimpleNamespace(room_id="room", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id=None),
    )))
    authority = ManualLightAuthority()
    authority.set_related_sources(RelatedLightSources.from_configs((config,)))
    assert authority.blocks("light.any")
    assert authority.barrier_reason_for("light.any") == "attribution_uncertain"
    authority.mark_unhealthy()
    assert authority.barrier_reason_for("light.any") == "storage_unavailable"


def test_related_light_sources_empty_ready_and_cross_room_collision_closed() -> None:
    assert RelatedLightSources.from_configs(()).ready
    room_a = SimpleNamespace(room_id="room_a", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id="light.shared"),
    )))
    room_b = SimpleNamespace(room_id="room_b", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id="light.shared"),
    )))
    graph = RelatedLightSources.from_configs((room_a, room_b))
    assert not graph.ready
    with pytest.raises(TypeError):
        graph.components["light.injected"] = frozenset(("light.injected",))


def test_related_evidence_prefers_own_then_latest_generation() -> None:
    config = SimpleNamespace(room_id="room", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id="light.a"), SimpleNamespace(entity_id="switch.b"),
        SimpleNamespace(entity_id="light.c"),
    )))
    authority = ManualLightAuthority()
    authority.set_related_sources(RelatedLightSources.from_configs((config,)))
    authority.capture("light.a", "on", "service", "a", observed_at_ms=1)
    authority.capture("switch.b", "off", "service", "b", observed_at_ms=2)
    assert authority.blocking_evidence("light.a").entity_id == "light.a"
    assert authority.blocking_evidence("light.c").entity_id == "switch.b"


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


async def test_explicit_return_requires_full_nonlegacy_manual_on_scope() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    config = SimpleNamespace(room_id="room", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id="light.primary"),
        SimpleNamespace(entity_id="switch.alternate"),
    )))
    authority.set_related_sources(RelatedLightSources.from_configs((config,)))
    authority.capture("light.primary", "on", "service", "on", observed_at_ms=1)
    await authority.async_drain()

    assert await authority.async_begin_explicit_return(("light.primary",)) is None
    token = await authority.async_begin_explicit_return(
        ("light.primary", "switch.alternate")
    )
    assert token is not None
    assert authority.blocks("light.primary")
    assert authority.barrier_reason == "return_pending"
    authority.async_finish_explicit_return(token, persisted=True)
    assert not authority.blocks("light.primary")
    assert authority.record("light.primary") is None
    assert store.payload["records"] == []


async def test_explicit_return_never_erases_manual_event_captured_during_save() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.writes = 0

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 3:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "old", observed_at_ms=1)
    await authority.async_drain()
    releasing = asyncio.create_task(
        authority.async_begin_explicit_return(("light.demo",))
    )
    await store.started.wait()
    assert authority.capture("light.demo", "off", "service", "new", observed_at_ms=2)
    store.release.set()
    token = await releasing
    assert token is not None
    authority.async_finish_explicit_return(token, persisted=True)
    assert authority.record("light.demo").action == "off"
    assert authority.blocks("light.demo")
    await authority.async_drain()
    assert store.payload["records"][0]["action"] == "off"


@pytest.mark.parametrize("new_action", ("on", "off"))
async def test_explicit_return_fence_rejects_event_drained_before_waiting_return(
    new_action: str,
) -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 3:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "old", observed_at_ms=1)
    await authority.async_drain()
    authority.capture("light.other", "on", "service", "other", observed_at_ms=2)
    draining = asyncio.create_task(authority.async_drain())
    await store.started.wait()
    returning = asyncio.create_task(
        authority.async_begin_explicit_return(("light.demo",))
    )
    await asyncio.sleep(0)
    assert authority.capture("light.demo", new_action, "service", "new", observed_at_ms=3)
    store.release.set()
    await draining

    assert await returning is None
    record = authority.record("light.demo")
    assert record is not None and record.action == new_action
    assert authority.blocks("light.demo")
    assert any(
        item["entityId"] == "light.demo" and item["action"] == new_action
        for item in store.payload["records"]
    )


async def test_explicit_return_persists_unrelated_pre_writer_fingerprint() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 3:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "old", observed_at_ms=1)
    await authority.async_drain()
    authority.capture("light.blocker", "on", "service", "blocker", observed_at_ms=2)
    draining = asyncio.create_task(authority.async_drain())
    await store.started.wait()
    returning = asyncio.create_task(
        authority.async_begin_explicit_return(("light.demo",))
    )
    await asyncio.sleep(0)
    assert authority.capture("light.other", "off", "service", "other", observed_at_ms=3)
    store.release.set()
    await draining
    token = await returning
    assert token is not None
    authority.async_finish_explicit_return(token, persisted=True)
    assert {item["entityId"] for item in store.payload["records"]} == {
        "light.blocker", "light.other"
    }
    assert "other" in store.payload["recentFingerprints"]

    restored = ManualLightAuthority(store)
    await restored.async_load()
    assert not restored.capture("light.other", "off", "service", "other", observed_at_ms=4)


async def test_explicit_return_store_failure_remains_global_deny() -> None:
    class _FailOnReleaseStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 3:
                raise RuntimeError("release storage")
            self.payload = payload

    authority = ManualLightAuthority(_FailOnReleaseStore())
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "on", observed_at_ms=1)
    await authority.async_drain()
    with pytest.raises(RuntimeError):
        await authority.async_begin_explicit_return(("light.demo",))
    assert authority.blocks("light.other")
    assert authority.barrier_reason == "storage_unavailable"


async def test_cancelled_explicit_return_remains_global_deny_and_token_is_scoped() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.writes = 0
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 3:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "on", observed_at_ms=1)
    await authority.async_drain()
    pending = asyncio.create_task(
        authority.async_begin_explicit_return(("light.demo",))
    )
    await store.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert authority.blocks("light.other")
    assert authority.barrier_reason == "storage_unavailable"
    with pytest.raises(RuntimeError):
        authority.async_finish_explicit_return(
            ManualLightReturnToken(frozenset(("light.demo",)), 1, 1),
            persisted=True,
        )


async def test_cancelled_room_history_save_remains_global_deny() -> None:
    authority = ManualLightAuthority(_Store())
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "on", observed_at_ms=1)
    await authority.async_drain()
    started = asyncio.Event()
    release = asyncio.Event()

    async def persist_room_history() -> bool:
        started.set()
        await release.wait()
        return True

    task = asyncio.create_task(
        ManualLightAutoReturn(authority).async_return(
            ("light.demo",),
            room_history_is_releasable=lambda: True,
            release_room_history=lambda: ("light-target",),
            persist_room_history=persist_room_history,
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert authority.blocks("light.other")
    assert authority.barrier_reason == "storage_unavailable"


async def test_legacy_import_is_idempotent_and_never_overwrites_newer_evidence() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    authority.capture("light.demo", "on", "service", "newer", observed_at_ms=20)
    assert not authority.capture_legacy("light.demo", source="room")
    await authority.async_drain()
    record = authority.record("light.demo")
    assert record is not None
    assert (record.source, record.fingerprint, record.observed_at_ms) == (
        "service", "newer", 20,
    )

    legacy = ManualLightAuthority(_Store())
    assert legacy.capture_legacy("light.legacy", source="room")
    assert not legacy.capture_legacy("light.legacy", source="room")
    await legacy.async_drain()
    assert legacy.record("light.legacy").source == "room"


async def test_legacy_import_racing_a_save_stays_pending_until_durable() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.writes = 0

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 1:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    assert authority.capture("light.new", "on", "service", "new")
    draining = asyncio.create_task(authority.async_drain())
    await store.started.wait()
    assert authority.capture_legacy("light.legacy", source="room")
    assert authority.blocks("light.legacy")
    store.release.set()
    await draining
    assert store.writes == 2
    assert {item["entityId"] for item in store.payload["records"]} == {
        "light.new", "light.legacy",
    }


async def test_dispatch_barrier_does_not_stop_durable_legacy_import() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    authority.begin_startup_observation()
    await authority.async_load()
    assert authority.unhealthy and authority.persistence_ready
    assert authority.capture_legacy("light.legacy", source="room")
    assert not authority.has_durable_import(("light.legacy",))
    authority.mark_unhealthy()
    await authority.async_drain()
    assert authority.has_durable_import(("light.legacy",))
    assert authority.blocks("light.other")
    assert store.payload["records"][0]["entityId"] == "light.legacy"


async def test_event_during_legacy_save_drains_while_startup_is_blocked() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.writes = 0

        async def async_save(self, payload):
            self.writes += 1
            if self.writes == 2:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    authority.begin_startup_observation()
    await authority.async_load()
    authority.capture_legacy("light.legacy", source="room")
    draining = asyncio.create_task(authority.async_drain())
    await store.started.wait()
    authority.capture("light.event", "on", "service", "event", observed_at_ms=1)
    assert not authority.has_durable_import(("light.legacy",))
    store.release.set()
    await draining
    assert store.writes == 3
    assert authority.has_durable_import(("light.legacy", "light.event"))
    assert {record["entityId"] for record in store.payload["records"]} == {
        "light.legacy", "light.event",
    }
    assert authority.blocks("light.unrelated")


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


async def test_room_context_child_trust_is_scoped_to_entity_and_action(monkeypatch) -> None:
    from custom_components.hausman_hub.application import room_lighting_ha_executor

    monkeypatch.setattr(
        room_lighting_ha_executor, "_new_service_context",
        lambda: SimpleNamespace(id="room-call"),
    )
    authority = ManualLightAuthority()
    executor = RoomLightingHaExecutor(manual_light_authority=authority)

    class _Hass:
        def __init__(self):
            self.tasks = []
            self.services = _Services()

        def async_create_task(self, coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task

    class _Services:
        async def async_call(self, domain, service, data, blocking=True, context=None):
            del blocking
            child = SimpleNamespace(id="child-call", parent_id=context.id)
            for entity_id, action, service_context in (
                ("light.a", "turn_on", context),
                ("light.a", "turn_on", child),
                ("light.b", "turn_on", child),
                ("light.a", "turn_off", child),
            ):
                listener.service_event(SimpleNamespace(
                    data={
                        "domain": domain, "service": action,
                        "service_data": {"entity_id": entity_id},
                    },
                    context=service_context,
                    time_fired=datetime.now(timezone.utc),
                ))

    hass = _Hass()
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: {"light.a", "light.b"},
        None, lambda: executor,
    )
    await executor._call(hass, "light", "turn_on", {"entity_id": "light.a"})
    assert authority.generation == 2
    assert authority.record("light.a").action == "off"
    assert authority.record("light.b").action == "on"
    assert executor.owns_service_context(
        SimpleNamespace(id="later", parent_id="room-call"), "light.a", "on"
    )
    assert not executor.owns_service_context(
        SimpleNamespace(id="later", parent_id="room-call"), "light.b", "on"
    )
    assert not executor.owns_service_context(
        SimpleNamespace(id="later", parent_id="room-call"), "light.a", "off"
    )
    await asyncio.gather(*hass.tasks)


def test_managed_room_light_scope_excludes_auxiliary_fan() -> None:
    config = SimpleNamespace(devices=SimpleNamespace(
        light_targets=(SimpleNamespace(entity_id="switch.room_lamp"),),
        auxiliaries=(SimpleNamespace(entity_id="switch.shower_fan"),),
    ))
    assert room_light_entity_ids((config,)) == frozenset({"switch.room_lamp"})


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
    assert not authority.unhealthy
    assert authority.blocks("switch.lamp")
    assert authority.barrier_reason_for("switch.lamp") == "selector_uncertain"
    await asyncio.gather(*hass.tasks)


def test_broad_entity_id_string_cannot_bypass_managed_light_attribution() -> None:
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        SimpleNamespace(), authority, lambda: {"light.demo"}, None, lambda: None
    )
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"entity_id": "all"}},
    ))
    assert not authority.unhealthy
    assert authority.blocks("light.demo")
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


def test_selector_uncertainty_is_domain_scoped_to_related_sources() -> None:
    config = SimpleNamespace(room_id="room", devices=SimpleNamespace(light_targets=(
        SimpleNamespace(entity_id="light.primary"),
        SimpleNamespace(entity_id="switch.alternate"),
    )))
    authority = ManualLightAuthority()
    authority.set_related_sources(RelatedLightSources.from_configs((config,)))
    authority.mark_selector_uncertain("light")
    assert authority.blocks("light.primary")
    assert authority.blocks("switch.alternate")
    assert not authority.blocks("switch.unrelated")
    assert authority.barrier_reason_for("light.primary") == "selector_uncertain"
    authority.invalidate_related_sources()
    assert authority.barrier_reason_for("light.primary") == "attribution_uncertain"


async def test_state_transition_never_clears_selector_uncertainty() -> None:
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        SimpleNamespace(), authority, lambda: {"light.demo"}, None, lambda: None,
    )
    authority.mark_selector_uncertain("light")
    observed = datetime.now(timezone.utc)
    listener.state_event(SimpleNamespace(
        data={
            "entity_id": "light.demo",
            "old_state": SimpleNamespace(state="off"),
            "new_state": SimpleNamespace(
                state="on", last_changed=observed, attributes={},
            ),
        },
        context=SimpleNamespace(id="manual-state"),
    ))
    assert authority.barrier_reason_for("light.demo") == "selector_uncertain"


def test_broad_or_malformed_service_never_captures_partial_manual_action() -> None:
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        SimpleNamespace(), authority,
        lambda: {"light.first", "light.second"}, None, lambda: None,
    )
    for service_data in (
        {},
        {"entity_id": []},
        {"entity_id": ("light.first",)},
        {"entity_id": ["light.first", "all"]},
        {"entity_id": ["light.first", "switch.other"]},
        {"entity_id": "light.all"},
        {"entity_id": "light.first,light.second"},
        {"entity_id": "light.*"},
        {"entity_id": "light.first "},
        {"entity_id": "light.first", "area_id": "area"},
        {"device_id": "device"},
        {"label_id": "label"},
        {"entity_id": "light.first", "floor_id": "floor"},
        {"entity_id": "light.first", "target": {"area_id": "area"}},
    ):
        listener.service_event(SimpleNamespace(
            data={"domain": "light", "service": "turn_on", "service_data": service_data},
            context=SimpleNamespace(id=f"broad-{service_data!r}"), time_fired=1,
        ))
    assert authority.record("light.first") is None
    assert authority.record("light.second") is None
    assert authority.barrier_reason_for("light.first") == "selector_uncertain"
    assert not authority.unhealthy


def test_outer_event_target_cannot_bypass_exact_service_data() -> None:
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        SimpleNamespace(), authority, lambda: {"light.first"}, None, lambda: None,
    )
    listener.service_event(SimpleNamespace(
        data={
            "domain": "light", "service": "turn_on",
            "service_data": {"entity_id": "light.first"},
            "target": {"area_id": "area"},
        },
        context=SimpleNamespace(id="outer-target"), time_fired=1,
    ))
    assert authority.record("light.first") is None
    assert authority.barrier_reason_for("light.first") == "selector_uncertain"


async def test_exact_service_list_captures_each_target_only() -> None:
    class _Hass:
        def __init__(self) -> None:
            self.tasks = []

        def async_create_task(self, coroutine):
            task = asyncio.create_task(coroutine)
            self.tasks.append(task)
            return task

    hass = _Hass()
    authority = ManualLightAuthority()
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: {"light.first", "light.second"}, None, lambda: None,
    )
    listener.service_event(SimpleNamespace(
        data={"domain": "light", "service": "turn_off", "service_data": {
            "entity_id": ["light.first", "light.second"],
        }},
        context=SimpleNamespace(id="exact-list"), time_fired=1,
    ))
    assert authority.record("light.first").action == "off"
    assert authority.record("light.second").action == "off"
    assert authority.barrier_reason_for("light.first") is None
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


async def test_first_startup_buffers_room_only_event_until_verified_save(monkeypatch) -> None:
    from custom_components.hausman_hub.application import room_lighting_ha_executor

    monkeypatch.setattr(
        room_lighting_ha_executor, "_new_service_context",
        lambda: SimpleNamespace(id="startup-automatic"),
    )
    callbacks = {}
    calls = []

    class _Bus:
        def async_listen(self, event_type, callback):
            callbacks[event_type] = callback
            return lambda: None

    class _Services:
        async def async_call(self, *args, **kwargs):
            calls.append((args, kwargs))

    hass = SimpleNamespace(
        bus=_Bus(), services=_Services(),
        states=SimpleNamespace(get=lambda _entity: None),
    )
    class _DelayedLoadStore(_Store):
        def __init__(self):
            super().__init__()
            self.load_started = asyncio.Event()
            self.load_release = asyncio.Event()

        async def async_load(self):
            self.load_started.set()
            await self.load_release.wait()
            return self.payload

    store = _DelayedLoadStore()
    authority = ManualLightAuthority(store)
    managed = set()
    listener = ManualLightAuthorityEvents(
        hass, authority, lambda: managed, None, lambda: None
    )
    listener.start(buffer_startup=True)
    loading = asyncio.create_task(authority.async_load())
    await store.load_started.wait()  # The setup await has begun.
    assert authority.blocks("light.room_only")
    callbacks["call_service"](SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"entity_id": "light.room_only"}},
        context=SimpleNamespace(id="external-startup-on"),
        time_fired=datetime.now(timezone.utc),
    ))
    store.load_release.set()
    await loading  # First install, verified empty seed.
    assert authority.seeded_empty_store
    # The room-only entity is not known yet. Startup denial covers the race.
    assert authority.record("light.room_only") is None
    target = LightTarget(
        id="room_only", name="Комнатный свет", kind=LightKind.LIGHT,
        brightness=False, color_temperature=False,
        entity_id="light.room_only",
    )
    executor = RoomLightingHaExecutor(
        {"room_only": target}, manual_light_authority=authority
    )
    command = PlannedCommand(
        "room_only", LightAction.TURN_OFF, reason=DecisionReason.SCHEDULE
    )
    assert not (await executor.execute(hass, command)).sent
    assert not calls
    managed.add("light.room_only")  # RoomLightingRuntime.start() completed.
    await listener.async_complete_startup()
    assert authority.record("light.room_only").action == "on"
    assert store.payload["records"][0]["entityId"] == "light.room_only"
    assert authority.blocks("light.room_only")
    assert not authority.blocks("light.unrelated")
    assert not (await executor.execute(hass, command)).sent
    assert not calls


async def test_empty_store_holds_preexisting_room_light_scope_only() -> None:
    store = _Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    assert authority.seeded_empty_store
    await authority.async_capture_initial_room_scope(
        ["light.configured", "switch.configured_relay"]
    )
    assert authority.record("light.configured").legacy
    assert authority.record("switch.configured_relay").legacy
    assert authority.blocks("light.configured")
    assert not authority.blocks("light.unconfigured")
    assert {item["entityId"] for item in store.payload["records"]} == {
        "light.configured", "switch.configured_relay",
    }

    restored = ManualLightAuthority(store)
    await restored.async_load()
    assert not restored.seeded_empty_store
    await restored.async_capture_initial_room_scope(["light.new"])
    assert restored.record("light.new") is None


async def test_room_only_state_transition_is_buffered_before_catalog_load() -> None:
    callbacks = {}

    class _Bus:
        def async_listen(self, event_type, callback):
            callbacks[event_type] = callback
            return lambda: None

    store = _Store()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    managed = set()
    listener = ManualLightAuthorityEvents(
        SimpleNamespace(bus=_Bus()), authority, lambda: managed, None, lambda: None
    )
    listener.start(buffer_startup=True)
    observed = datetime.now(timezone.utc)
    callbacks["state_changed"](SimpleNamespace(
        data={
            "entity_id": "switch.room_only",
            "old_state": SimpleNamespace(state="on"),
            "new_state": SimpleNamespace(
                state="off", last_changed=observed, attributes={},
            ),
        },
        context=SimpleNamespace(id="external-state-off"),
    ))
    assert authority.record("switch.room_only") is None
    assert authority.blocks("switch.room_only")
    managed.add("switch.room_only")
    await listener.async_complete_startup()
    assert authority.record("switch.room_only").action == "off"
    assert store.payload["records"][0]["entityId"] == "switch.room_only"


async def test_room_only_event_during_startup_drain_is_not_lost() -> None:
    class _SlowStore(_Store):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.delay = False

        async def async_save(self, payload):
            if self.delay:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    store.delay = True
    managed = {"light.room_a", "light.room_b"}
    callbacks = {}

    class _Bus:
        def async_listen(self, event_type, callback):
            callbacks[event_type] = callback
            return lambda: None

    listener = ManualLightAuthorityEvents(
        SimpleNamespace(bus=_Bus()), authority, lambda: managed, None, lambda: None
    )
    listener.start(buffer_startup=True)
    callbacks["call_service"](SimpleNamespace(
        data={"domain": "light", "service": "turn_off",
              "service_data": {"entity_id": "light.room_a"}},
        context=SimpleNamespace(id="first"), time_fired=1,
    ))
    completion = asyncio.create_task(listener.async_complete_startup())
    await store.started.wait()
    assert authority.blocks("light.unrelated")  # Save not verified yet.
    callbacks["call_service"](SimpleNamespace(
        data={"domain": "light", "service": "turn_on",
              "service_data": {"entity_id": "light.room_b"}},
        context=SimpleNamespace(id="second"), time_fired=2,
    ))
    assert authority.blocks("light.room_b")
    store.release.set()
    await completion
    assert {item["entityId"] for item in store.payload["records"]} == managed
    assert authority.record("light.room_b").action == "on"
    assert not authority.blocks("light.unrelated")


async def test_selector_during_startup_drain_is_applied_before_dispatch_opens() -> None:
    class _SlowStore(_Store):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.delay = False

        async def async_save(self, payload):
            if self.delay:
                self.started.set()
                await self.release.wait()
            self.payload = payload

    store = _SlowStore()
    authority = ManualLightAuthority(store)
    await authority.async_load()
    store.delay = True
    callbacks = {}

    class _Bus:
        def async_listen(self, event_type, callback):
            callbacks[event_type] = callback
            return lambda: None

    listener = ManualLightAuthorityEvents(
        SimpleNamespace(bus=_Bus()), authority, lambda: {"light.room_a"},
        None, lambda: None,
    )
    listener.start(buffer_startup=True)
    callbacks["call_service"](SimpleNamespace(
        data={"domain": "light", "service": "turn_on", "service_data": {
            "entity_id": "light.room_a",
        }},
        context=SimpleNamespace(id="startup-exact"), time_fired=1,
    ))
    completion = asyncio.create_task(listener.async_complete_startup())
    await store.started.wait()
    callbacks["call_service"](SimpleNamespace(
        data={"domain": "light", "service": "turn_on", "service_data": {
            "area_id": "unknown",
        }},
        context=SimpleNamespace(id="startup-selector"), time_fired=2,
    ))
    store.release.set()
    await completion
    assert authority.record("light.room_a").action == "on"
    assert not authority.unhealthy
    assert authority.barrier_reason_for("light.room_a") == "selector_uncertain"


def _assert_startup_source_order(source: str) -> None:
    functions = {node.name: node for node in ast.parse(source).body
                 if isinstance(node, ast.AsyncFunctionDef)}
    setup = ast.get_source_segment(source, functions["async_setup_entry"])
    starter = ast.get_source_segment(source, functions["_async_start_room_lighting_with_core"])
    assert "await room_lighting_runtime.start(hass, entry.entry_id)" in starter
    assert setup.index("manual_light_events.start(buffer_startup=True)") < setup.index(
        "await manual_light_authority.async_load()"
    ) < setup.index(
        "await _async_start_room_lighting_with_core("
    ) < setup.index("await manual_light_events.async_complete_startup()") < setup.index(
        "activation_latch.open()"
    )
    assert setup.index("await room_lighting_service.async_list_configs()") < setup.index(
        "await manual_light_authority.async_capture_initial_room_scope("
    ) < setup.index("await manual_light_events.async_complete_startup()")


def test_startup_wiring_drains_room_events_before_activation() -> None:
    source = (Path(__file__).parents[1] / "custom_components" /
              "hausman_hub" / "__init__.py").read_text(encoding="utf-8")
    _assert_startup_source_order(source)


@pytest.mark.parametrize("swapped", [(1, 4), (5, 6), (2, 3)])
def test_startup_source_order_still_rejects_unsafe_activation(swapped) -> None:
    calls = [
        "manual_light_events.start(buffer_startup=True)",
        "await manual_light_authority.async_load()",
        "await room_lighting_service.async_list_configs()",
        "await manual_light_authority.async_capture_initial_room_scope()",
        "await _async_start_room_lighting_with_core()",
        "await manual_light_events.async_complete_startup()",
        "activation_latch.open()",
    ]
    first, second = swapped
    calls[first], calls[second] = calls[second], calls[first]
    source = ("async def _async_start_room_lighting_with_core():\n"
              "    await room_lighting_runtime.start(hass, entry.entry_id)\n"
              "async def async_setup_entry():\n" +
              "\n".join("    " + call for call in calls) + "\n")
    with pytest.raises(AssertionError):
        _assert_startup_source_order(source)


def test_drain_from_worker_thread_is_scheduled_on_loop_threadsafe() -> None:
    """Live regression 2026-09-29: the authority drain is captured from
    a worker thread (HA dispatches sync event listeners there); the
    drain must be scheduled threadsafe on the captured loop, actually
    awaited, and the capture must land in the store (SEC-REPAIR-03)."""

    import json

    async def scenario() -> dict:
        saved: list = []

        class _MemoryStore:
            recovered_previous = False

            async def async_load(self):
                return None

            async def async_save(self, payload):
                saved.append(json.loads(json.dumps(payload, default=str)))

        hass_calls: list = []
        hass = SimpleNamespace(
            async_create_task=lambda task: hass_calls.append(
                asyncio.get_event_loop().create_task(task)
            )
        )
        authority = ManualLightAuthority(_MemoryStore())
        listener = ManualLightAuthorityEvents(
            hass,
            authority,
            lambda: frozenset({"light.stand_main"}),
            None,
            lambda: None,
        )
        listener.start()  # called inside the running loop, as in setup
        worker_error: list = []

        def worker() -> None:
            try:
                # The worker thread captures a manual action, as HA's
                # sync event dispatch does in production.
                listener._capture_evidence(
                    _StartupEvidence(
                        "light.stand_main", "on", "state", "ctx-worker",
                        "event-worker-1",
                    )
                )
            except Exception as error:  # pragma: no cover
                worker_error.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        assert not worker_error, worker_error

        for _ in range(50):
            if hass_calls and all(task.done() for task in hass_calls):
                break
            await asyncio.sleep(0.01)
        results = await asyncio.gather(*hass_calls, return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        assert not failures, failures
        assert len(hass_calls) == 1, "the drain task must be scheduled once"
        # The capture actually landed in the authority store
        # (SEC-REPAIR-03): the drain persisted it.
        assert saved, "the drain did not persist the capture"
        return {"saved": saved}

    result = asyncio.run(scenario())
    assert result["saved"]


def test_closed_listener_drops_drain_and_marks_unhealthy() -> None:
    """cancel() closes the listener: a drain captured before cancel is
    dropped (no task on an unloaded integration) and the authority is
    marked unhealthy so manual light automation stays denied
    (SEC-REPAIR-02)."""

    async def scenario() -> dict:
        class _MemoryStore:
            recovered_previous = False

            async def async_load(self):
                return None

            async def async_save(self, payload):
                self.payload = payload

        store = _MemoryStore()
        authority = ManualLightAuthority(store)
        listener = ManualLightAuthorityEvents(
            SimpleNamespace(async_create_task=None),
            authority,
            lambda: frozenset({"light.stand_main"}),
            None,
            lambda: None,
        )
        listener.start()
        listener.cancel()
        listener._capture_evidence(
            _StartupEvidence(
                "light.stand_main", "on", "state", "ctx-closed",
                "event-closed-1",
            )
        )
        assert authority.unhealthy
        return {"saved": getattr(store, "payload", None)}

    result = asyncio.run(scenario())
    assert result["saved"] is None


def test_cancel_flushes_pending_capture_exactly_once() -> None:
    """cancel() flushes already captured actions through the final drain
    exactly once, even though the listener is closed (SEC-REPAIR-02R1);
    captures after cancel are dropped."""

    import json

    async def scenario() -> dict:
        saved: list = []
        schedule_calls: list = []

        class _MemoryStore:
            recovered_previous = False

            async def async_load(self):
                return None

            async def async_save(self, payload):
                saved.append(json.loads(json.dumps(payload, default=str)))

        hass = SimpleNamespace(
            async_create_task=lambda task: schedule_calls.append(task)
        )
        authority = ManualLightAuthority(_MemoryStore())
        listener = ManualLightAuthorityEvents(
            hass,
            authority,
            lambda: frozenset({"light.stand_main"}),
            None,
            lambda: None,
        )
        listener.start()
        listener._capture_evidence(
            _StartupEvidence(
                "light.stand_main", "on", "state", "ctx-flush",
                "event-flush-1",
            )
        )
        listener.cancel()
        listener._capture_evidence(
            _StartupEvidence(
                "light.stand_main", "on", "state", "ctx-after-cancel",
                "event-after-cancel-1",
            )
        )
        # The final drain is scheduled on the loop; yield once so it runs.
        await asyncio.sleep(0.01)
        assert schedule_calls, "final drain was not scheduled at cancel"
        assert len(schedule_calls) == 1, "cancel scheduled more than one drain"
        await asyncio.gather(*schedule_calls, return_exceptions=True)
        failures = [r for r in schedule_calls if isinstance(r, BaseException)]
        assert not failures, failures
        # The pending capture survived the unload; the late one did not.
        assert saved, "pending capture was lost at cancel"
        joined = json.dumps(saved, ensure_ascii=False)
        assert "ctx-flush" in joined
        assert "ctx-after-cancel" not in joined

    asyncio.run(scenario())

def test_stopped_loop_marks_unhealthy_instead_of_wrong_thread() -> None:
    """A dead loop must not send hass.async_create_task into a worker
    thread again: the drain coroutine is closed and the authority is
    marked unhealthy (SEC-REPAIR-01)."""

    async def scenario() -> dict:
        class _MemoryStore:
            recovered_previous = False

            async def async_load(self):
                return None

            async def async_save(self, payload):
                self.payload = payload

        store = _MemoryStore()
        authority = ManualLightAuthority(store)
        listener = ManualLightAuthorityEvents(
            SimpleNamespace(async_create_task=None),
            authority,
            lambda: frozenset({"light.stand_main"}),
            None,
            lambda: None,
        )
        listener.start()
        listener._loop = None  # stopped/unknown loop
        listener._capture_evidence(
            _StartupEvidence(
                "light.stand_main", "on", "state", "ctx-dead",
                "event-dead-1",
            )
        )
        assert authority.unhealthy
        return {"saved": getattr(store, "payload", None)}

    result = asyncio.run(scenario())
    assert result["saved"] is None
