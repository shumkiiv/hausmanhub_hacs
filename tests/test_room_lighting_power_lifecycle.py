"""Whole power-delivery transitions, with real shadow persistence and fake I/O."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_room_lighting_runtime as fixture


pytestmark = pytest.mark.asyncio


def make_case(untrusted=False):
    helper = fixture.TestPowerSourceDeliveryEpisode()
    clock = {"value": fixture._NOW_MS + 60_000}
    runtime, hass = (
        helper._runtime_untrusted(clock) if untrusted else helper._runtime(clock)
    )
    runtime._electrical_breaker_resolver = lambda _entity: False
    helper._confirmed_presence(runtime, fixture._NOW_MS - 20_000)
    helper._latch(runtime, fixture._NOW_MS - 9_000)
    return helper, runtime, hass, clock


async def deliver(helper, runtime, clock, path, room_id=None):
    kwargs = dict(
        at=clock["value"], room_id=room_id or helper.ROOM,
        operation_id="whole-cycle-test", config_version=1, command_action="turn_on",
    )
    if path == "exact":
        return await runtime._ensure_power_source(
            fixture.DevicePowerDependency(
                dependent_entity_id="light.demo_main",
                power_source_entity_id=helper.POWER,
                policy="auto_turn_on", warmup_seconds=0,
            ),
            **kwargs,
        )
    return await runtime._ensure_legacy_room_power(helper.POWER, **kwargs)


async def record_outer(helper, runtime, clock, detail, *, target_attempt=False):
    await runtime._shadow.async_record_dispatch(
        at=clock["value"], room_id=helper.ROOM,
        receipts=[{
            "targetId": "light_main", "evidenceRevision": "target-test",
            "action": "turn_on", "dispatchAttempt": target_attempt,
            "sendOutcome": "sent" if target_attempt else "not_sent",
            "_attemptAt": clock["value"], "power": detail,
        }],
        operation_id="whole-cycle-test", config_version=1,
    )


async def reload_runtime(helper, runtime, hass, clock):
    shadow = fixture.RoomLightingShadowService(runtime._shadow._store)
    await shadow.async_load()
    restored = fixture._make_runtime(
        hass, commands_enabled=True, now_ms=lambda: clock["value"]
    )
    restored._configs = dict(runtime._configs)
    restored._shadow = shadow
    helper._confirmed_presence(restored, fixture._NOW_MS - 20_000)
    restored._restore_dispatch_failures()
    return restored


@pytest.mark.parametrize("path", ["exact", "legacy"])
@pytest.mark.parametrize("untrusted", [False, True])
@pytest.mark.parametrize("refusal", ["presence", "away", "reserved", "pending_store", "not_sent"])
async def test_refusal_preserves_the_entire_delivery_cycle(path, untrusted, refusal):
    helper, runtime, hass, clock = make_case(untrusted)
    original_stamp = runtime._power_failure_at[helper.POWER]
    sends = []

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, hass, entity, **kwargs):
            sends.append(entity)
            return {"confirmed": False, "sent": False, "sendOutcome": "not_sent",
                    "reason": "executor_refusal"}

    runtime._executor = Executor()
    original = runtime._persist_pending_power_dispatch

    async def pending(**kwargs):
        if refusal == "pending_store":
            return False
        saved = await original(**kwargs)
        if refusal == "presence":
            hass.states.set("binary_sensor.demo_presence", "off",
                            last_changed=fixture._dt(clock["value"]))
        elif refusal == "away":
            hass.states.set(fixture.AWAY_ENTITY_ID, "unavailable",
                            last_changed=fixture._dt(clock["value"]))
        elif refusal == "reserved":
            runtime._reserved_entity_ids_provider = lambda: {helper.POWER}
        return saved

    runtime._persist_pending_power_dispatch = pending
    powered, detail = await deliver(helper, runtime, clock, path)
    assert not powered
    assert len(sends) == (1 if refusal == "not_sent" else 0)
    assert detail["dispatchAttempt"] is False
    assert helper.POWER not in runtime._power_delivery
    assert runtime._power_failure_at[helper.POWER] == original_stamp
    failures = runtime._shadow.journal_payload()["dispatchFailures"]
    assert len(failures) == 1
    assert failures[0]["blockedAfterMs"] == original_stamp
    await record_outer(helper, runtime, clock, detail, target_attempt=True)
    failures = runtime._shadow.journal_payload()["dispatchFailures"]
    assert {item["kind"] for item in failures} == {"power", "target"}
    assert next(item for item in failures if item["kind"] == "power")["blockedAfterMs"] == original_stamp
    restored = await reload_runtime(helper, runtime, hass, clock)
    assert restored._power_attempt_blocked(
        helper.POWER, room_id=helper.ROOM, command_action="turn_on"
    )
    assert restored._power_delivery[helper.POWER].exhausted
    for entry in runtime._shadow.journal_payload()["entries"]:
        for receipt in entry.get("dispatch", []):
            assert not any(key.startswith("_") for key in receipt.get("power", {}))


@pytest.mark.parametrize("path", ["exact", "legacy"])
@pytest.mark.parametrize("outcome", ["sent", "uncertain", "exception"])
async def test_possible_send_is_counted_once_and_survives_restart(path, outcome):
    helper, runtime, hass, clock = make_case()

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, hass, entity, **kwargs):
            if outcome == "exception":
                raise RuntimeError("transport outcome unknown")
            return {"confirmed": False, "sent": outcome == "sent", "sendOutcome": outcome}

    runtime._executor = Executor()
    original = runtime._persist_pending_power_dispatch

    async def delayed(**kwargs):
        saved = await original(**kwargs)
        clock["value"] += 9_000
        return saved

    runtime._persist_pending_power_dispatch = delayed
    powered, detail = await deliver(helper, runtime, clock, path)
    assert not powered
    assert runtime._power_delivery[helper.POWER].attempts == 1
    assert runtime._power_failure_at[helper.POWER] == clock["value"]
    await record_outer(helper, runtime, clock, detail)
    restored = await reload_runtime(helper, runtime, hass, clock)
    assert restored._power_delivery[helper.POWER].exhausted
    assert restored._power_attempt_blocked(
        helper.POWER, room_id=helper.ROOM, command_action="turn_on"
    )


@pytest.mark.parametrize("path", ["exact", "legacy"])
@pytest.mark.parametrize("first_success", [False, True])
async def test_late_room_receipt_cannot_overwrite_newer_source_result(path, first_success):
    helper, runtime, hass, clock = make_case()

    class Executor(fixture._SpyExecutor):
        success = first_success

        async def async_power_on(self, hass, entity, **kwargs):
            return {"confirmed": self.success, "sent": True, "sendOutcome": "sent"}

    executor = Executor()
    runtime._executor = executor
    _, old_detail = await deliver(helper, runtime, clock, path)
    assert old_detail["_durable_recorded"] is True
    clock["value"] += fixture.POWER_RETRY_INTERVAL_MS + 1
    executor.success = not first_success
    # Another room shares this relay. Its completion precedes the old outer receipt.
    other = replace(runtime._configs[helper.ROOM], room_id="other_room")
    runtime._configs[other.room_id] = other
    runtime._curve_presence[other.room_id] = dict(runtime._curve_presence[helper.ROOM])
    _, new_detail = await deliver(helper, runtime, clock, path, room_id=other.room_id)
    before = list(runtime._shadow.journal_payload()["dispatchFailures"])
    await record_outer(helper, runtime, clock, old_detail)
    assert runtime._shadow.journal_payload()["dispatchFailures"] == before
    assert bool(before) is first_success
    assert new_detail["_durable_recorded"] is True


@pytest.mark.parametrize("path", ["exact", "legacy"])
async def test_shared_source_has_three_actual_spaced_retries(path):
    helper, runtime, hass, clock = make_case(untrusted=True)
    calls = []

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, hass, entity, **kwargs):
            calls.append(clock["value"])
            return {"confirmed": False, "sent": True, "sendOutcome": "sent"}

    runtime._executor = Executor()
    for index in range(3):
        await deliver(helper, runtime, clock, path)
        assert len(calls) == index + 1
        await deliver(helper, runtime, clock, path)
        assert len(calls) == index + 1
        clock["value"] += fixture.POWER_RETRY_INTERVAL_MS
    other = replace(runtime._configs[helper.ROOM], room_id="other_room")
    runtime._configs[other.room_id] = other
    runtime._curve_presence[other.room_id] = dict(runtime._curve_presence[helper.ROOM])
    await deliver(helper, runtime, clock, path, room_id=other.room_id)
    assert len(calls) == 3
    assert runtime._power_delivery[helper.POWER].attempts == 3
    assert all(b - a >= fixture.POWER_RETRY_INTERVAL_MS for a, b in zip(calls, calls[1:]))


@pytest.mark.parametrize("path", ["exact", "legacy"])
async def test_final_storage_failure_stays_closed_in_memory_and_after_reload(path):
    helper, runtime, hass, clock = make_case()
    save = runtime._shadow._store.async_save
    writes = []

    async def fail_final(payload):
        writes.append(payload)
        if len(writes) > 1:
            raise OSError("final write unavailable")
        await save(payload)

    runtime._shadow._store.async_save = fail_final
    powered, detail = await deliver(helper, runtime, clock, path)
    assert not powered
    assert detail["reason"] == "power_result_persistence_failed"
    assert detail.get("_durable_recorded") is not True
    clock["value"] += fixture.POWER_RETRY_INTERVAL_MS + 1
    assert runtime._power_attempt_blocked(
        helper.POWER, room_id=helper.ROOM, command_action="turn_on"
    )
    restored = await reload_runtime(helper, runtime, hass, clock)
    assert restored._power_delivery[helper.POWER].exhausted


async def test_admission_queries_have_no_mutating_side_effects():
    helper, runtime, hass, clock = make_case()
    original = dict(runtime._power_failures), dict(runtime._power_failure_at)
    for _ in range(10):
        assert not runtime._power_attempt_blocked(
            helper.POWER, room_id=helper.ROOM, command_action="turn_on"
        )
    assert (runtime._power_failures, runtime._power_failure_at) == original
    assert not runtime._power_delivery


@pytest.mark.parametrize("receipt, expected", [
    ({"confirmed": False, "sent": False}, (False, False, "uncertain")),
    ({"confirmed": True, "sent": True}, (False, True, "sent")),
    ({"confirmed": True, "sendOutcome": "unknown"}, (False, False, "uncertain")),
    ({"confirmed": True, "sent": True, "sendOutcome": "not_sent"}, (False, True, "sent")),
    ({"confirmed": True, "sent": False, "sendOutcome": "sent"}, (False, False, "uncertain")),
    ({"confirmed": True, "sent": False, "sendOutcome": "uncertain"}, (False, False, "uncertain")),
    ({"confirmed": True, "sent": True, "sendOutcome": []}, (False, True, "sent")),
    ({"confirmed": True, "sent": False, "sendOutcome": "not_sent"}, (True, False, "not_sent")),
    ({"confirmed": False, "sent": False, "sendOutcome": "not_sent"}, (False, False, "not_sent")),
    ({"confirmed": True, "sent": True, "sendOutcome": "sent"}, (True, True, "sent")),
    (SimpleNamespace(confirmed="yes", sent="yes", send_outcome="sent"), (False, False, "uncertain")),
    (SimpleNamespace(confirmed=True, sent=False), (False, False, "uncertain")),
    (True, (True, True, "sent")),
    (False, (False, False, "uncertain")),
])
async def test_receipt_normalization_requires_explicit_consistent_evidence(receipt, expected):
    _, runtime, _, _ = make_case()
    assert runtime._power_receipt_outcome(receipt) == expected


@pytest.mark.parametrize("path", ["exact", "legacy"])
@pytest.mark.parametrize("receipt", [
    {"confirmed": False, "sent": False},
    {"confirmed": True, "sendOutcome": "unknown"},
    {"confirmed": True, "sent": False, "sendOutcome": "sent"},
])
async def test_incomplete_executor_answer_is_uncertain_and_durable(path, receipt):
    helper, runtime, hass, clock = make_case()

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, hass, entity, **kwargs):
            return receipt

    runtime._executor = Executor()
    powered, detail = await deliver(helper, runtime, clock, path)
    assert not powered
    assert detail["sendOutcome"] == "uncertain"
    assert runtime._power_delivery[helper.POWER].attempts == 1
    assert runtime._command_grace[helper.POWER] == ("turn_on", clock["value"])
    restored = await reload_runtime(helper, runtime, hass, clock)
    assert restored._power_delivery[helper.POWER].exhausted


@pytest.mark.parametrize("path", ["exact", "legacy"])
@pytest.mark.parametrize("previous", [None, ("turn_off", fixture._NOW_MS)])
@pytest.mark.parametrize("newer", [False, True])
async def test_proven_not_sent_restores_only_its_own_grace(path, previous, newer):
    helper, runtime, hass, clock = make_case()
    if previous is not None:
        runtime._command_grace[helper.POWER] = previous
    new_grace = ("turn_on", clock["value"] + 1)

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, hass, entity, **kwargs):
            if newer:
                runtime._command_grace[entity] = new_grace
            return {"confirmed": False, "sent": False, "sendOutcome": "not_sent"}

    runtime._executor = Executor()
    powered, _ = await deliver(helper, runtime, clock, path)
    assert not powered
    assert runtime._command_grace.get(helper.POWER) == (new_grace if newer else previous)


@pytest.mark.parametrize("path", ["exact", "legacy"])
async def test_executor_fresh_already_on_proof_needs_no_send_or_new_grace(path):
    helper, runtime, hass, clock = make_case()
    previous = ("turn_off", fixture._NOW_MS)
    runtime._command_grace[helper.POWER] = previous

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, hass, entity, **kwargs):
            return {"confirmed": True, "sent": False, "sendOutcome": "not_sent"}

    runtime._executor = Executor()
    powered, detail = await deliver(helper, runtime, clock, path)
    assert powered
    assert detail["dispatchAttempt"] is False
    assert helper.POWER not in runtime._power_failures
    assert helper.POWER not in runtime._power_delivery
    assert runtime._command_grace[helper.POWER] == previous
    assert runtime._shadow.journal_payload()["dispatchFailures"] == []
