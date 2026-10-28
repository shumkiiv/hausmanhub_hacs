"""Day-curve power admission must remain closed while Away is active."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import os

import pytest
import test_room_lighting_power_lifecycle as lifecycle
import test_room_lighting_runtime as fixture


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("path", ("exact", "legacy"))
@pytest.mark.parametrize("after_pending", (False, True))
async def test_day_curve_away_blocks_power_before_and_after_pending(path, after_pending):
    helper, runtime, hass, clock = lifecycle.make_case()
    runtime._configs[helper.ROOM] = replace(runtime._configs[helper.ROOM], profile="day_curve")
    runtime._electrical_breaker_resolver = lambda _entity: False
    sends = []

    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, _hass, entity, **_kwargs):
            sends.append(entity)
            return {"confirmed": True, "sent": True, "sendOutcome": "sent"}

    runtime._executor = Executor()
    original = runtime._persist_pending_power_dispatch

    async def pending(**kwargs):
        saved = await original(**kwargs)
        if after_pending:
            runtime._away = True
        return saved

    runtime._persist_pending_power_dispatch = pending
    if not after_pending:
        runtime._away = True
    powered, detail = await lifecycle.deliver(helper, runtime, clock, path)
    assert powered is False
    assert sends == []
    assert detail["reason"] == "profile_blocked"


def _snapshot(runtime):
    return deepcopy((runtime._power_failures, runtime._power_failure_at,
                     runtime._power_delivery, runtime._command_grace))


def _assert_not_sent(powered, detail, runtime, sends):
    assert powered is False
    assert sends == [] and runtime._executor.calls == []
    assert (detail["sent"], detail["dispatchAttempt"], detail["sendOutcome"]) == (False, False, "not_sent")
    assert "_attemptAt" not in detail


def _executor(runtime, sends, *, confirmed=True):
    class Executor(fixture._SpyExecutor):
        async def async_power_on(self, _hass, entity, **_kwargs):
            sends.append(entity)
            return {"confirmed": confirmed, "sent": True, "sendOutcome": "sent"}
    runtime._executor = Executor()


@pytest.mark.parametrize("path", ("exact", "legacy"))
async def test_away_changes_while_really_waiting_for_source_lock_without_spending_attempt(path):
    helper, runtime, _hass, clock = lifecycle.make_case()
    runtime._configs[helper.ROOM] = replace(runtime._configs[helper.ROOM], profile="day_curve")
    sends, pending = [], []
    _executor(runtime, sends)
    persist = runtime._persist_pending_power_dispatch
    async def observe_pending(**kwargs):
        pending.append(kwargs)
        return await persist(**kwargs)
    runtime._persist_pending_power_dispatch = observe_pending
    before = _snapshot(runtime)
    journal_before = deepcopy(runtime._shadow.journal_payload())
    entered = asyncio.Event()
    lock = runtime._power_source_lock(helper.POWER)
    await lock.acquire()
    own_lock = True
    async def attempt():
        entered.set()
        return await lifecycle.deliver(helper, runtime, clock, path)
    task = asyncio.create_task(attempt())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.sleep(0)
        assert lock.locked() and runtime._power_source_lock(helper.POWER) is lock
        assert lock._waiters and sum(not waiter.done() for waiter in lock._waiters) == 1
        assert not task.done() and pending == [] and sends == []
        assert runtime._shadow.journal_payload() == journal_before
        assert _snapshot(runtime) == before
        runtime._away = True
        lock.release()
        own_lock = False
        powered, detail = await asyncio.wait_for(task, 2)
        _assert_not_sent(powered, detail, runtime, sends)
        assert detail["reason"] == "profile_blocked" and pending == []
        assert _snapshot(runtime) == before
    finally:
        if own_lock:
            lock.release()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class FileBackend:
    """Test-only disk boundary: every load decodes the real file anew."""
    def __init__(self, path):
        self.path = path
        self.loads = 0
        self.attempts = []
        self.saved = []
        self.fail_at = None

    async def async_load(self):
        self.loads += 1
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    async def async_save(self, payload):
        self.attempts.append(deepcopy(payload))
        if self.fail_at is not None and len(self.attempts) >= self.fail_at:
            raise OSError("synthetic final write unavailable")
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        self.saved.append(deepcopy(payload))


def _assert_shadow_document(shadow, expected):
    assert expected["mode"] == "live"
    assert expected["commandsEnabled"] is True
    assert shadow._document(True) == expected
    public_expected = deepcopy(expected)
    public_expected.update(mode="shadow", commandsEnabled=False)
    assert shadow.journal_payload() == public_expected


async def _real_file_failure(tmp_path, path):
    helper, runtime, hass, clock = lifecycle.make_case()
    runtime._configs[helper.ROOM] = replace(runtime._configs[helper.ROOM], profile="day_curve")
    backend = FileBackend(tmp_path / "shadow.json")
    shadow = fixture.RoomLightingShadowService(backend)
    await shadow.async_load()
    assert backend.loads == 1 and shadow.journal_payload()["entries"] == []
    runtime._shadow = shadow
    sends = []
    _executor(runtime, sends, confirmed=False)
    powered, detail = await lifecycle.deliver(helper, runtime, clock, path)
    assert powered is False and sends == [helper.POWER]
    assert detail["sent"] is True and detail["sendOutcome"] == "sent"
    assert detail["reason"] == "power_source_unconfirmed"
    document = await backend.async_load()
    _assert_shadow_document(shadow, document)
    assert len(document["entries"]) == 2 and len(document["dispatchFailures"]) == 1
    failure = document["dispatchFailures"][0]
    assert failure == {
        "key": f"power:{helper.POWER}", "kind": "power", "entityId": helper.POWER,
        "action": "turn_on", "evidenceRevision": detail["evidenceRevision"],
        "blockedAfterMs": clock["value"],
    }
    assert runtime._power_delivery[helper.POWER].attempts == 1
    assert runtime._executor.calls == []
    sends.clear()
    clock["value"] += fixture.POWER_RETRY_INTERVAL_MS + 1
    return helper, runtime, hass, clock, backend, sends, document


def _pending_entry(helper, clock, revision):
    return {
        "at": clock["value"], "roomId": helper.ROOM, "mode": "live", "commandsEnabled": True,
        "dispatchPending": {"kind": "power", "sourceEntityId": helper.POWER,
            "action": "turn_on", "evidenceRevision": revision, "reason": "pending_power_dispatch"},
        "operationId": "whole-cycle-test", "configVersion": 1,
    }


def _public_refusal(helper, revision, path):
    value = dict(sourceEntityId=helper.POWER, evidenceRevision=revision, reason="profile_blocked",
                 dispatchAttempt=False, sent=False, sendOutcome="not_sent", deliveryEpisodeOpen=True)
    if path == "exact":
        value.update(policy="auto_turn_on", warmupSeconds=0, sourceTurnedOn=False, waitedSeconds=0.0)
    return value


def _pending_document(before, helper, clock, revision):
    expected = deepcopy(before)
    expected["entries"].append(_pending_entry(helper, clock, revision))
    expected["dispatchFailures"] = [{
        "key": f"power:{helper.POWER}", "kind": "power", "roomId": helper.ROOM,
        "entityId": helper.POWER, "action": "turn_on", "evidenceRevision": revision,
        "blockedAfterMs": clock["value"], "reason": "pending_power_dispatch",
    }]
    return expected


async def _disk_reload(helper, runtime, hass, clock, file_path, expected):
    backend = FileBackend(file_path)
    shadow = fixture.RoomLightingShadowService(backend)
    await shadow.async_load()
    assert backend.loads == 1
    _assert_shadow_document(shadow, expected)
    restored = fixture._make_runtime(hass, commands_enabled=True, now_ms=lambda: clock["value"])
    restored._configs = dict(runtime._configs)
    restored._shadow = shadow
    helper._confirmed_presence(restored, fixture._NOW_MS - 20_000)
    restored._restore_dispatch_failures()
    assert restored._power_attempt_blocked(helper.POWER, room_id=helper.ROOM, command_action="turn_on")
    assert restored._power_delivery[helper.POWER].exhausted is True
    assert restored._power_failures[helper.POWER] == ("turn_on", expected["dispatchFailures"][0]["evidenceRevision"])
    return restored


@pytest.mark.parametrize("path", ("exact", "legacy"))
async def test_real_file_pending_refusal_preserves_old_failure_and_late_outer_after_reload(tmp_path, path):
    helper, runtime, hass, clock, backend, sends, before = await _real_file_failure(tmp_path, path)
    previous = deepcopy(before["dispatchFailures"][0])
    revision = previous["evidenceRevision"]
    memory_before = _snapshot(runtime)
    persist = runtime._persist_pending_power_dispatch
    pending_saved = []
    async def pending(**kwargs):
        saved = await persist(**kwargs)
        assert saved is True
        pending_saved.append(await backend.async_load())
        runtime._away = True
        return saved
    runtime._persist_pending_power_dispatch = pending
    powered, detail = await lifecycle.deliver(helper, runtime, clock, path)
    _assert_not_sent(powered, detail, runtime, sends)
    assert detail["reason"] == "profile_blocked"
    assert detail["_preservedFailure"] == previous and detail["_durable_recorded"] is True
    assert _snapshot(runtime) == memory_before
    assert pending_saved == [_pending_document(before, helper, clock, revision)]
    public_power = _public_refusal(helper, revision, path)
    expected = deepcopy(before)
    expected["entries"].extend([
        _pending_entry(helper, clock, revision),
        {"at": clock["value"], "roomId": helper.ROOM, "mode": "live", "commandsEnabled": True,
         "dispatch": [{"power": public_power}], "operationId": "whole-cycle-test", "configVersion": 1},
    ])
    assert await backend.async_load() == expected
    _assert_shadow_document(runtime._shadow, expected)
    assert "_preservedFailure" not in json.dumps(expected)
    restored = await _disk_reload(helper, runtime, hass, clock, backend.path, expected)
    assert restored._shadow is not runtime._shadow and restored._shadow._store is not backend
    # A late room receipt must append its public result, never replay source state.
    await lifecycle.record_outer(helper, runtime, clock, detail)
    expected["entries"].append({
        "at": clock["value"], "roomId": helper.ROOM, "mode": "live", "commandsEnabled": True,
        "dispatch": [{"targetId": "light_main", "evidenceRevision": "target-test", "action": "turn_on",
                      "dispatchAttempt": False, "sendOutcome": "not_sent", "power": public_power}],
        "operationId": "whole-cycle-test", "configVersion": 1,
    })
    assert await backend.async_load() == expected
    _assert_shadow_document(runtime._shadow, expected)
    assert expected["dispatchFailures"] == [previous]
    assert "_preservedFailure" not in json.dumps(expected)
    await _disk_reload(helper, runtime, hass, clock, backend.path, expected)
    _assert_not_sent(powered, detail, runtime, sends)


@pytest.mark.parametrize("path", ("exact", "legacy"))
async def test_final_save_failure_keeps_actual_file_pending_and_new_runtime_closed(tmp_path, path):
    helper, runtime, hass, clock, backend, sends, before = await _real_file_failure(tmp_path, path)
    revision = before["dispatchFailures"][0]["evidenceRevision"]
    persist = runtime._persist_pending_power_dispatch
    async def pending(**kwargs):
        saved = await persist(**kwargs)
        assert saved is True
        runtime._away = True
        return saved
    runtime._persist_pending_power_dispatch = pending
    backend.fail_at = len(backend.attempts) + 2
    powered, detail = await lifecycle.deliver(helper, runtime, clock, path)
    _assert_not_sent(powered, detail, runtime, sends)
    assert detail["reason"] == "power_result_persistence_failed"
    assert detail.get("_durable_recorded") is not True
    assert runtime._power_delivery[helper.POWER].exhausted is True
    assert runtime._power_attempt_blocked(helper.POWER, room_id=helper.ROOM, command_action="turn_on")
    expected = _pending_document(before, helper, clock, revision)
    assert await backend.async_load() == expected
    _assert_shadow_document(runtime._shadow, expected)
    assert backend.saved[-1] == expected and backend.attempts[-1] != expected
    assert backend.path.exists() and expected["dispatchFailures"][0]["reason"] == "pending_power_dispatch"
    await _disk_reload(helper, runtime, hass, clock, backend.path, expected)


@pytest.mark.parametrize("path", ("exact", "legacy"))
@pytest.mark.parametrize("profile, away", [("day_curve", False), (None, True)])
async def test_home_day_curve_and_legacy_non_day_curve_keep_normal_send(path, profile, away):
    helper, runtime, _hass, clock = lifecycle.make_case()
    runtime._configs[helper.ROOM] = replace(runtime._configs[helper.ROOM], profile=profile)
    runtime._away = away
    sends = []
    _executor(runtime, sends)
    powered, detail = await lifecycle.deliver(helper, runtime, clock, path)
    assert powered is True and sends == [helper.POWER]
    assert runtime._executor.calls == []
    assert detail["reason"] == "power_source_on"
    assert (detail["sent"], detail["dispatchAttempt"], detail["sendOutcome"]) == (True, True, "sent")


@pytest.mark.parametrize("path", ("exact", "legacy"))
@pytest.mark.parametrize("guard, reason", [("unknown_away", "away_sensor_unavailable"),
                                          ("reserved", "power_source_reserved"), ("quiet", "quiet_hours")])
async def test_existing_pre_send_policy_refusals_stay_closed_on_both_paths(path, guard, reason):
    helper, runtime, hass, clock = lifecycle.make_case()
    runtime._configs[helper.ROOM] = replace(runtime._configs[helper.ROOM], profile="day_curve")
    if guard == "unknown_away":
        hass.states.set(fixture.AWAY_ENTITY_ID, "unavailable", last_changed=fixture._dt(clock["value"]))
    elif guard == "reserved":
        runtime._reserved_entity_ids_provider = lambda: {helper.POWER}
    else:
        payload = fixture._config_payload()
        payload["quietHours"] = {"start": "00:00", "end": "23:59"}
        runtime._configs[helper.ROOM] = fixture.config_from_payload(payload)
        assert runtime._configs[helper.ROOM].profile is None
        assert runtime._quiet_hours_active(runtime._configs[helper.ROOM])
    before = _snapshot(runtime)
    sends = []
    _executor(runtime, sends)
    powered, detail = await lifecycle.deliver(helper, runtime, clock, path)
    _assert_not_sent(powered, detail, runtime, sends)
    assert detail["reason"] == reason and _snapshot(runtime) == before
