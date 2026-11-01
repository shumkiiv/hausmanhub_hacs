"""Тесты подсказки о выключенном опросе доступности Zigbee2MQTT."""

import asyncio
import json
import logging
from types import SimpleNamespace

from custom_components.hausman_hub.adapters import (
    z2m_availability_advisory as z2m_module,
)
from custom_components.hausman_hub.adapters.z2m_availability_advisory import (
    ISSUE_ID,
    MAX_PAYLOAD_SIZE,
    RETRY_DELAY_SECONDS,
    RETAIN_ISSUE_ID,
    SUBSCRIBE_TIMEOUT_SECONDS,
    TOPIC,
    Z2mAvailabilityAdvisory,
    _NoopAdvisory,
    _mqtt_ready,
    _subscribe_with_limits,
    decide_availability_advice,
    decide_state_retain_advice,
    start_z2m_availability_advisory,
)

_ADVISORY_LOGGER = (
    "custom_components.hausman_hub.adapters.z2m_availability_advisory"
)


def _payload(enabled):
    return json.dumps({"config": {"availability": {"enabled": enabled}}})


def _padded_payload(enabled, total):
    """Valid JSON of exactly ``total`` characters (leading spaces pad it)."""
    body = '{"config": {"availability": {"enabled": %s}}}' % (
        "true" if enabled else "false"
    )
    return " " * (total - len(body)) + body


def _fake_ha_mqtt(
    ready_result=None,
    wait_delay=0.0,
    subscribe_delay=0.0,
    unsub_calls=None,
    qos_box=None,
):
    async def wait(hass):
        if wait_delay:
            await asyncio.sleep(wait_delay)
        return ready_result

    async def subscribe(hass, topic, callback, qos):
        if qos_box is not None:
            qos_box.append((topic, qos))
        if subscribe_delay:
            await asyncio.sleep(subscribe_delay)

        def unsub():
            if unsub_calls is not None:
                unsub_calls.append(1)

        return unsub

    return SimpleNamespace(
        async_wait_for_mqtt_client=wait,
        async_subscribe=subscribe,
    )


async def _null_subscribe(hass, topic, callback):
    return None


def _make_advisory(create_calls, delete_calls, unsubs):
    subscribed = []

    async def subscribe(hass, topic, callback):
        subscribed.append(topic)
        unsubs.append({"calls": 0})

        def unsub():
            unsubs[-1]["calls"] += 1

        return unsub

    return (
        Z2mAvailabilityAdvisory(
            SimpleNamespace(), subscribe=subscribe,
            create_issue=lambda hass: create_calls.append(True),
            delete_issue=lambda hass: delete_calls.append(True),
        ),
        subscribed,
    )


async def test_disabled_availability_creates_issue_and_subscribes_retained_topic():
    create, delete, unsubs = [], [], []
    advisory, subscribed = _make_advisory(create, delete, unsubs)
    await advisory.start()
    assert subscribed == [TOPIC]
    advisory._handle_message(SimpleNamespace(payload=_payload(False)))
    assert create == [True]
    assert delete == []
    await advisory.async_close()
    assert unsubs[0]["calls"] == 1


async def test_enabled_availability_deletes_issue():
    create, delete, unsubs = [], [], []
    advisory, _ = _make_advisory(create, delete, unsubs)
    advisory._handle_message(SimpleNamespace(payload=_payload(True)))
    assert create == []
    assert delete == [True]


async def test_unknown_payloads_never_create_or_delete():
    create, delete, unsubs = [], [], []
    advisory, _ = _make_advisory(create, delete, unsubs)
    for payload in (
        None, 123, b"\xff\xfe", "", "not json", "[]", '"text"',
        "{}", '{"config": null}', '{"config": {}}',
        '{"config": {"availability": {}}}',
        '{"config": {"availability": {"enabled": "false"}}}',
        '{"config": {"availability": {"enabled": null}}}',
        '{"config": {"availability": {"enabled": 1}}}',
    ):
        advisory._handle_message(SimpleNamespace(payload=payload))
    assert create == [] and delete == []


async def test_bytes_payload_with_false_still_creates():
    create, delete, unsubs = [], [], []
    advisory, _ = _make_advisory(create, delete, unsubs)
    advisory._handle_message(SimpleNamespace(payload=_payload(False).encode()))
    assert create == [True]


async def test_decision_helper_direct_table():
    assert decide_availability_advice(_payload(False)) == "create"
    assert decide_availability_advice(_payload(True)) == "delete"
    assert decide_availability_advice(_payload("false")) is None
    assert decide_availability_advice(b"") is None
    assert decide_availability_advice(42) is None


async def test_payload_size_boundary_rejects_all_oversized_types():
    ok = _padded_payload(False, MAX_PAYLOAD_SIZE)
    assert decide_availability_advice(ok) == "create"
    ok_bytes = ok.encode()
    assert decide_availability_advice(ok_bytes) == "create"
    assert decide_availability_advice(bytearray(ok_bytes)) == "create"

    over = _padded_payload(False, MAX_PAYLOAD_SIZE + 1)
    # The oversized payload is VALID JSON: if it reached the parser the
    # hint would be created, so None proves the limit rejected it early.
    assert decide_availability_advice(over) is None
    assert decide_availability_advice(over.encode()) is None
    assert decide_availability_advice(bytearray(over.encode())) is None


async def test_deeply_nested_json_returns_unknown_without_exception():
    # Deep enough to trigger recursion inside json.loads, yet under the
    # size limit so the nesting path itself is exercised.
    deep = "[" * 30000 + "1" + "]" * 30000
    assert len(deep) <= MAX_PAYLOAD_SIZE
    assert decide_availability_advice(deep) is None


async def test_recursion_error_branch_is_isolated(monkeypatch):
    def boom(data):
        raise RecursionError("depth")

    monkeypatch.setattr(z2m_module.json, "loads", boom)
    assert decide_availability_advice(_payload(False)) is None


async def test_issue_id_is_stable_and_matches_translation_key():
    assert ISSUE_ID == "z2m_availability_disabled"


async def test_advisory_constants_and_topics():
    assert TOPIC == "zigbee2mqtt/bridge/info"
    assert MAX_PAYLOAD_SIZE == 65536
    assert SUBSCRIBE_TIMEOUT_SECONDS == 5


async def test_mqtt_ready_semantics():
    # HA signals success with None (or True); only explicit False is fatal.
    assert _mqtt_ready(None) is True
    assert _mqtt_ready(True) is True
    assert _mqtt_ready(False) is False


async def test_real_subscribe_path_readiness_none_true_subscribe_false_aborts():
    for ready, should_subscribe in ((None, True), (True, True), (False, False)):
        unsub_calls, boxes = [], []
        transport = _fake_ha_mqtt(
            ready_result=ready, unsub_calls=unsub_calls, qos_box=boxes,
        )
        unsub = await _subscribe_with_limits(
            transport, SimpleNamespace(), TOPIC, lambda message: None, 1,
        )
        if should_subscribe:
            assert callable(unsub)
            assert boxes == [(TOPIC, 0)]
        else:
            assert unsub is None
            assert boxes == []


async def test_real_subscribe_path_hangs_time_out():
    # The single bounded flow covers the client wait AND the subscribe call.
    transport_wait = _fake_ha_mqtt(wait_delay=5.0)
    assert await _subscribe_with_limits(
        transport_wait, SimpleNamespace(), TOPIC, lambda message: None, 0.3,
    ) is None
    transport_sub = _fake_ha_mqtt(
        ready_result=None, subscribe_delay=5.0,
    )
    assert await _subscribe_with_limits(
        transport_sub, SimpleNamespace(), TOPIC, lambda message: None, 0.3,
    ) is None


async def test_repeated_false_messages_use_single_domain_issue_pair():
    pairs = []
    registry = {}

    def create_port(hass):
        pair = ("hausman_hub", ISSUE_ID)
        pairs.append(pair)
        registry.setdefault(pair, {"count": 0})["count"] += 1

    def delete_port(hass):
        pairs.append(("hausman_hub", ISSUE_ID))
        registry.pop(("hausman_hub", ISSUE_ID), None)

    advisory = Z2mAvailabilityAdvisory(
        SimpleNamespace(),
        subscribe=_null_subscribe,
        create_issue=create_port,
        delete_issue=delete_port,
    )
    await advisory.start()
    for _ in range(3):
        advisory._handle_message(SimpleNamespace(payload=_payload(False)))
    assert len(registry) == 1
    assert registry[("hausman_hub", ISSUE_ID)]["count"] == 3
    assert all(pair == ("hausman_hub", ISSUE_ID) for pair in pairs)
    advisory._handle_message(SimpleNamespace(payload=_payload(True)))
    assert registry == {}


async def test_issue_survives_close():
    create, delete, unsubs = [], [], []
    advisory, _ = _make_advisory(create, delete, unsubs)
    await advisory.start()
    advisory._handle_message(SimpleNamespace(payload=_payload(False)))
    assert create == [True]
    await advisory.async_close()
    # Stop must not remove the hint.
    assert delete == []


async def test_raising_ports_are_isolated_without_leaking_content(caplog):
    marker_payload = "PAYLOAD-MARKER-9f3b"
    marker_error = "ERROR-MARKER-2c81"

    def create_port(hass):
        raise RuntimeError(f"broken {marker_error}")

    def delete_port(hass):
        raise RuntimeError(f"broken {marker_error}")

    advisory = Z2mAvailabilityAdvisory(
        SimpleNamespace(),
        subscribe=_null_subscribe,
        create_issue=create_port,
        delete_issue=delete_port,
    )
    await advisory.start()
    with caplog.at_level(logging.DEBUG, logger=_ADVISORY_LOGGER):
        advisory._handle_message(
            SimpleNamespace(
                payload='{"leak": "%s", "config": {"availability": '
                '{"enabled": false}}}' % marker_payload,
            ),
        )
        advisory._handle_message(SimpleNamespace(payload=_payload(True)))
    assert caplog.text == "" or (
        marker_payload not in caplog.text and marker_error not in caplog.text
    )


async def test_messages_after_close_ignored_and_restart_does_not_subscribe():
    create, delete, unsubs = [], [], []
    advisory, subscribed = _make_advisory(create, delete, unsubs)
    await advisory.start()
    await advisory.async_close()
    advisory._handle_message(SimpleNamespace(payload=_payload(False)))
    assert create == []
    await advisory.start()
    assert subscribed == [TOPIC]  # no second subscription


async def test_close_without_start_is_safe():
    create, delete, unsubs = [], [], []
    advisory, _ = _make_advisory(create, delete, unsubs)
    advisory.close()
    await advisory.async_close()
    assert create == [] and delete == []


async def test_failing_unsubscribe_never_raises_and_is_attempted_once():
    create, delete, unsubs = [], [], []
    advisory, subscribed = _make_advisory(create, delete, unsubs)
    await advisory.start()
    attempts = []

    def broken_unsub():
        attempts.append(1)
        raise RuntimeError("transport gone")

    advisory._unsub = broken_unsub
    advisory.close()
    assert attempts == [1]
    assert advisory._unsub is None
    assert advisory._closed is True


async def test_close_twice_calls_unsubscriber_once():
    create, delete, unsubs = [], [], []
    advisory, subscribed = _make_advisory(create, delete, unsubs)
    await advisory.start()
    advisory.close()
    advisory.close()
    assert unsubs[0]["calls"] == 1


async def test_late_subscription_completion_after_close_is_cancelled():
    release = asyncio.Event()
    calls = []

    async def subscribe(hass, topic, callback):
        await release.wait()

        def unsub():
            calls.append("late-unsub")

        return unsub

    advisory = Z2mAvailabilityAdvisory(
        SimpleNamespace(), subscribe=subscribe,
        create_issue=lambda hass: None,
        delete_issue=lambda hass: None,
    )
    task = asyncio.create_task(advisory.start())
    await asyncio.sleep(0)
    advisory.close()
    release.set()
    await task
    # The subscription created after close is cancelled immediately.
    assert calls == ["late-unsub"]
    assert advisory._unsub is None


async def test_sync_close_schedules_awaitable_result_when_hass_supports_it():
    scheduled = []
    awaited = []

    async def unsub():
        awaited.append("awaited")

    async def subscribe(hass, topic, callback):
        return unsub

    hass = SimpleNamespace(async_create_task=scheduled.append)
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=subscribe,
        create_issue=lambda h: None,
        delete_issue=lambda h: None,
    )
    await advisory.start()
    advisory.close()
    assert awaited == []  # sync close does not block on the awaitable
    assert len(scheduled) == 1
    await scheduled[0]
    assert awaited == ["awaited"]


async def test_start_with_failed_transport_keeps_advisory_inert():
    create, delete = [], []

    async def subscribe(hass, topic, callback):
        return None  # transport never became ready

    advisory = Z2mAvailabilityAdvisory(
        SimpleNamespace(), subscribe=subscribe,
        create_issue=lambda hass: create.append(True),
        delete_issue=lambda hass: delete.append(True),
    )
    await advisory.start()
    assert advisory._unsub is None
    advisory.close()
    assert create == [] and delete == []


async def test_homeassistant_wiring_degrades_to_noop_without_ha():
    # In this repository's test environment homeassistant is not installed:
    # the wiring must degrade to the no-op advisory instead of raising.
    advisory = await start_z2m_availability_advisory(SimpleNamespace())
    assert isinstance(advisory, _NoopAdvisory)
    advisory.close()
    await advisory.async_close()


async def test_noop_advisory_close_sync_and_async():
    noop = _NoopAdvisory()
    noop.close()
    await noop.async_close()


def _retrying_subscribe(attempts, unsubs, fail_first=True):
    async def subscribe(hass, topic, callback):
        attempts.append(1)
        if fail_first and len(attempts) == 1:
            return None  # MQTT client not ready yet

        def unsub():
            unsubs.append(1)

        return unsub

    return subscribe


def _retry_harness():
    timers = []

    def schedule_retry(action):
        timers.append({"action": action, "cancelled": False, "fired": 0})

        def cancel():
            timers[-1]["cancelled"] = True

        return cancel

    return timers, schedule_retry


def _task_recording_hass():
    scheduled = []

    def create_task(coro):
        task = asyncio.get_event_loop().create_task(coro)
        scheduled.append(task)
        return task

    return SimpleNamespace(async_create_task=create_task), scheduled


async def test_initial_success_creates_no_retry_timer():
    attempts, unsubs = [], []
    timers, schedule_retry = _retry_harness()
    hass, scheduled = _task_recording_hass()
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=_retrying_subscribe(attempts, unsubs, fail_first=False),
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    assert attempts == [1]
    assert timers == []
    assert advisory._unsub is not None
    advisory.close()
    assert unsubs == [1]


async def test_first_failure_schedules_one_retry_firing_gives_second_attempt():
    attempts, unsubs = [], []
    timers, schedule_retry = _retry_harness()
    hass, scheduled = _task_recording_hass()
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=_retrying_subscribe(attempts, unsubs),
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    assert attempts == [1]
    assert len(timers) == 1  # exactly one retry scheduled
    assert advisory._unsub is None

    timers[0]["fired"] += 1
    timers[0]["action"](None)  # timer elapsed
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert attempts == [1, 1]  # exactly a second attempt
    assert advisory._unsub is not None
    advisory.close()
    assert unsubs == [1]


async def test_second_failure_does_not_schedule_third_attempt():
    attempts = []

    async def subscribe(hass, topic, callback):
        attempts.append(1)
        return None  # never ready

    timers, schedule_retry = _retry_harness()
    hass, scheduled = _task_recording_hass()
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=subscribe,
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    assert len(timers) == 1
    timers[0]["action"](None)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert len(attempts) == 2
    assert len(timers) == 1  # no third attempt was scheduled
    # A second manual fire is ignored (cancel reference already consumed).
    timers[0]["action"](None)
    await asyncio.sleep(0)
    assert len(attempts) == 2


async def test_close_before_timer_cancels_it_and_forced_fire_does_nothing():
    attempts, unsubs = [], []
    timers, schedule_retry = _retry_harness()
    hass, scheduled = _task_recording_hass()
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=_retrying_subscribe(attempts, unsubs),
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    advisory.close()
    assert timers[0]["cancelled"] is True
    timers[0]["action"](None)  # forced late fire must do nothing
    await asyncio.sleep(0)
    assert attempts == [1]
    assert scheduled == []


async def test_close_during_retry_attempt_releases_late_subscription():
    release = asyncio.Event()
    attempts, unsubs = [], []

    async def subscribe(hass, topic, callback):
        attempts.append(1)
        if len(attempts) == 1:
            return None
        await release.wait()

        def unsub():
            unsubs.append(1)

        return unsub

    timers, schedule_retry = _retry_harness()
    hass, scheduled = _task_recording_hass()
    create, delete = [], []
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=subscribe,
        create_issue=lambda h: create.append(True),
        delete_issue=lambda h: delete.append(True),
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    timers[0]["action"](None)
    await asyncio.sleep(0)  # retry attempt starts and awaits release
    advisory.close()
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert unsubs == [1]  # late subscription cancelled immediately
    assert advisory._unsub is None
    advisory._handle_message(SimpleNamespace(payload=_payload(False)))
    assert create == [] and delete == []


async def test_repeated_start_and_close_do_not_multiply_attempts():
    attempts, unsubs = [], []
    timers, schedule_retry = _retry_harness()
    hass, scheduled = _task_recording_hass()
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=_retrying_subscribe(attempts, unsubs),
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    await advisory.start()  # no-op: already started
    advisory.close()
    advisory.close()
    timers[0]["action"](None)  # cancelled timer fires anyway
    await asyncio.sleep(0)
    assert attempts == [1]  # no retry after close
    assert unsubs == []  # the first attempt never subscribed
    assert len(scheduled) == 0


async def test_failing_retry_cancel_does_not_block_unsubscribe():
    attempts, unsubs = [], []

    def schedule_retry(action):
        def cancel():
            raise RuntimeError("timer cancel failed")

        return cancel

    hass = SimpleNamespace()
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=_retrying_subscribe(attempts, unsubs),
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()  # first attempt fails, retry scheduled
    advisory._unsub = lambda: unsubs.append(1)
    advisory.close()  # must not raise despite the failing cancel
    assert advisory._closed is True
    assert unsubs == [1]


async def test_close_after_retry_queued_but_before_it_runs_cancels_attempt():
    attempts, unsubs = [], []
    timers, schedule_retry = _retry_harness()
    queued = []
    hass = SimpleNamespace(async_create_task=queued.append)  # no scheduling
    advisory = Z2mAvailabilityAdvisory(
        hass, subscribe=_retrying_subscribe(attempts, unsubs),
        create_issue=lambda h: None, delete_issue=lambda h: None,
        schedule_retry=schedule_retry,
    )
    await advisory.start()
    assert attempts == [1]
    assert len(timers) == 1
    timers[0]["action"](None)  # timer fires: retry task queued, not run
    assert len(queued) == 1
    advisory.close()  # closes while the retry task is still queued
    await queued[0]  # the queued coroutine finally runs — must do nothing
    assert attempts == [1]  # no second subscription attempt
    assert unsubs == []
    assert advisory._unsub is None
    assert advisory._attempt_in_flight is False
    assert advisory._retry_cancel is None
    assert len(timers) == 1


async def test_real_wiring_wraps_callback_and_schedules_retry(monkeypatch):
    import sys
    import types

    marked = []
    received = []
    timers = []

    def fake_callback(fn):
        fn.__ha_pinned_callback__ = True
        marked.append(fn)
        return fn

    mod_mqtt = types.ModuleType("homeassistant.components.mqtt")
    wait_state = {"ready": None}

    async def fake_wait(hass):
        return wait_state["ready"]

    async def fake_subscribe(hass, topic, cb, qos):
        received.append((topic, qos, getattr(cb, "__ha_pinned_callback__",
                                            False)))
        return lambda: None

    mod_mqtt.async_wait_for_mqtt_client = fake_wait
    mod_mqtt.async_subscribe = fake_subscribe

    mod_core = types.ModuleType("homeassistant.core")
    mod_core.callback = fake_callback

    mod_ir = types.ModuleType("homeassistant.helpers.issue_registry")
    mod_ir.IssueSeverity = SimpleNamespace(WARNING="warning")
    mod_ir.async_create_issue = lambda *a, **k: None
    mod_ir.async_delete_issue = lambda *a, **k: None

    mod_ev = types.ModuleType("homeassistant.helpers.event")

    def fake_call_later(hass_, delay, action):
        timers.append((delay, getattr(action, "__ha_pinned_callback__",
                                      False)))
        return lambda: None

    mod_ev.async_call_later = fake_call_later

    mod_comps = types.ModuleType("homeassistant.components")
    mod_comps.mqtt = mod_mqtt
    mod_helpers = types.ModuleType("homeassistant.helpers")
    mod_helpers.issue_registry = mod_ir
    mod_helpers.event = mod_ev
    mod_pkg = types.ModuleType("homeassistant")
    mod_pkg.components = mod_comps
    mod_pkg.helpers = mod_helpers

    for name, module in (
        ("homeassistant", mod_pkg),
        ("homeassistant.components", mod_comps),
        ("homeassistant.components.mqtt", mod_mqtt),
        ("homeassistant.core", mod_core),
        ("homeassistant.helpers", mod_helpers),
        ("homeassistant.helpers.issue_registry", mod_ir),
        ("homeassistant.helpers.event", mod_ev),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    advisory = await start_z2m_availability_advisory(SimpleNamespace())
    assert isinstance(advisory, Z2mAvailabilityAdvisory)
    assert received == [(TOPIC, 0, True)]  # topic, QoS0, pinned callback
    assert timers == []  # client was ready: no retry timer
    advisory.close()

    # Second scenario: the client never became ready -> exactly one retry
    # is scheduled through the HA timer with the marked action.
    wait_state["ready"] = False
    received.clear()
    advisory2 = await start_z2m_availability_advisory(SimpleNamespace())
    assert isinstance(advisory2, Z2mAvailabilityAdvisory)
    assert received == []  # no subscription without a ready client
    assert timers == [(RETRY_DELAY_SECONDS, True)]  # marked retry action
    advisory2.close()


# --- state retention hint (same subscription, second issue) ---------------


def _retain_payload(retain, *, with_device_options=True,
                    with_availability=True):
    config = {"availability": {"enabled": True}} if with_availability else {}
    if with_device_options:
        config["device_options"] = {} if retain is _MISSING else {"retain": retain}
    return json.dumps({"config": config})


def _retain_raw(config):
    """Full control over the config object for damaged-value tests."""
    return json.dumps({"config": config})


class _MISSING:
    """Sentinel: omit the ``retain`` key entirely."""


def _make_dual_advisory(availability_create, availability_delete,
                        retain_create, retain_delete, unsubs):
    subscribed = []

    async def subscribe(hass, topic, callback):
        subscribed.append(topic)
        unsubs.append({"calls": 0})

        def unsub():
            unsubs[-1]["calls"] += 1

        return unsub

    advisory = Z2mAvailabilityAdvisory(
        SimpleNamespace(), subscribe=subscribe,
        create_issue=lambda hass: availability_create.append(True),
        delete_issue=lambda hass: availability_delete.append(True),
        create_retain_issue=lambda hass: retain_create.append(True),
        delete_retain_issue=lambda hass: retain_delete.append(True),
    )
    return advisory, subscribed


def test_retain_issue_id_constant():
    assert z2m_module.RETAIN_ISSUE_ID == "z2m_state_retain_disabled"


async def test_retain_false_creates_issue_true_deletes():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)
    advisory._handle_message(SimpleNamespace(payload=_retain_payload(False)))
    assert r_create == [True] and r_delete == []
    advisory._handle_message(SimpleNamespace(payload=_retain_payload(True)))
    assert r_create == [True] and r_delete == [True]
    # The well-formed retain payload carries availability enabled=true, so
    # the independent availability hint legitimately deleted its own issue.
    assert a_create == [] and a_delete == [True, True]


async def test_retain_missing_key_or_missing_device_options_creates():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)
    advisory._handle_message(
        SimpleNamespace(payload=_retain_payload(_MISSING)))
    advisory._handle_message(
        SimpleNamespace(payload=_retain_payload(True, with_device_options=False)))
    assert r_create == [True, True] and r_delete == []


async def test_retain_damaged_device_options_value_is_unknown():
    # A present-but-damaged device_options value must neither create nor
    # delete a hint; the availability hint keeps working on the same message.
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)
    for damaged in (None, "nope", [], 5, True):
        payload = json.dumps({"config": {"availability": {"enabled": False},
                                         "device_options": damaged}})
        advisory._handle_message(SimpleNamespace(payload=payload))
    assert r_create == [] and r_delete == []
    assert a_create == [True] * 5


async def test_retain_decision_helper_direct_table():
    assert decide_state_retain_advice(_retain_payload(False)) == "create"
    assert decide_state_retain_advice(_retain_payload(True)) == "delete"
    assert decide_state_retain_advice(_retain_payload(_MISSING)) == "create"
    assert decide_state_retain_advice(
        _retain_payload(True, with_device_options=False)) == "create"
    # Missing device_options key inside a valid config -> create.
    assert decide_state_retain_advice(
        _retain_raw({"availability": {"enabled": True}})) == "create"
    # Present-but-damaged device_options value -> unknown.
    for damaged in (None, "nope", [], 5, True):
        assert decide_state_retain_advice(
            _retain_raw({"device_options": damaged})) is None, damaged
    # Non-boolean retain inside a dict -> unknown.
    for bad in (0, 1, None, "true", [], {}, 0.0):
        assert decide_state_retain_advice(_retain_payload(bad)) is None, bad


async def test_retain_malformed_payloads_do_nothing():
    for broken in ("not json", "[1, 2]", '"str"', "null",
                   json.dumps({"config": "nope"}),
                   json.dumps({"other": {}})):
        assert decide_state_retain_advice(broken) is None, broken
    assert decide_state_retain_advice(b"\xff\xfe") is None


async def test_retain_oversized_payload_rejected():
    body = '{"config": {"device_options": {"retain": false}}}'
    ok = " " * (MAX_PAYLOAD_SIZE - len(body)) + body
    over = " " * (MAX_PAYLOAD_SIZE - len(body) + 1) + body
    assert decide_state_retain_advice(ok) == "create"
    assert decide_state_retain_advice(over) is None
    assert decide_state_retain_advice(over.encode()) is None


async def test_both_hints_independent_on_one_message():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _subscribed = _make_dual_advisory(
        a_create, a_delete, r_create, r_delete, unsubs)
    combined = json.dumps({"config": {"availability": {"enabled": False},
                                      "device_options": {"retain": True}}})
    advisory._handle_message(SimpleNamespace(payload=combined))
    assert a_create == [True] and r_delete == [True]
    assert a_delete == [] and r_create == []
    combined2 = json.dumps({"config": {"availability": {"enabled": True},
                                       "device_options": {"retain": False}}})
    advisory._handle_message(SimpleNamespace(payload=combined2))
    assert a_delete == [True] and r_create == [True]


async def test_retain_port_exception_isolated():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    subscribed = []

    async def subscribe(hass, topic, callback):
        subscribed.append(topic)

        def unsub():
            unsubs.append(1)

        return unsub

    def broken_availability_create(_hass):
        raise RuntimeError("availability boom")

    advisory = Z2mAvailabilityAdvisory(
        SimpleNamespace(), subscribe=subscribe,
        create_issue=broken_availability_create,
        delete_issue=lambda hass: a_delete.append(True),
        create_retain_issue=lambda hass: r_create.append(True),
        delete_retain_issue=lambda hass: r_delete.append(True),
    )
    combined = json.dumps({"config": {"availability": {"enabled": False},
                                      "device_options": {"retain": False}}})
    advisory._handle_message(SimpleNamespace(payload=combined))
    assert r_create == [True]
    assert a_create == [] and a_delete == [] and r_delete == []


async def test_retain_issue_survives_close():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)
    advisory._handle_message(SimpleNamespace(payload=_retain_payload(False)))
    advisory.close()
    advisory._handle_message(SimpleNamespace(payload=_retain_payload(True)))
    assert r_create == [True] and r_delete == []


async def test_advisory_without_retain_ports_keeps_legacy_behavior():
    create, delete, unsubs = [], [], []
    advisory, _ = _make_advisory(create, delete, unsubs)
    # No availability section: the legacy advisory has nothing to decide,
    # and with no retain ports the retention hint is silently skipped.
    advisory._handle_message(SimpleNamespace(
        payload=_retain_payload(False, with_availability=False)))
    advisory._handle_message(SimpleNamespace(
        payload=_retain_payload(True, with_availability=False)))
    assert create == [] and delete == []


async def test_translations_contain_retain_issue_keys():
    import pathlib

    base = (pathlib.Path(z2m_module.__file__).resolve().parent.parent
            / "translations")
    for loc in ("en", "ru"):
        data = json.loads((base / f"{loc}.json").read_text(encoding="utf-8"))
        issue = data["issues"]["z2m_state_retain_disabled"]
        assert issue["title"] and issue["description"]
        assert issue["title"] != issue["description"]


# --- R3: failure of one hint must never suppress the other ----------------


def _both_false_payload():
    return json.dumps({
        "config": {
            "availability": {"enabled": False},
            "device_options": {"retain": False},
        }
    })


async def test_retain_port_failure_does_not_suppress_availability_issue():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)

    def broken_create(hass):
        raise RuntimeError("retain registry port failed")

    advisory._create_retain_issue = broken_create
    advisory._handle_message(SimpleNamespace(payload=_both_false_payload()))
    assert a_create == [True]          # the other hint still worked
    assert r_create == []              # the broken port consumed nothing


async def test_availability_port_failure_does_not_suppress_retain_issue():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)

    def broken_create(hass):
        raise RuntimeError("availability registry port failed")

    advisory._create_issue = broken_create
    advisory._handle_message(SimpleNamespace(payload=_both_false_payload()))
    assert r_create == [True]
    assert a_create == []


async def test_retain_delete_failure_does_not_suppress_availability_delete():
    a_create, a_delete, r_create, r_delete, unsubs = [], [], [], [], []
    advisory, _ = _make_dual_advisory(a_create, a_delete, r_create, r_delete, unsubs)

    def broken_delete(hass):
        raise RuntimeError("retain delete failed")

    advisory._delete_retain_issue = broken_delete
    advisory._handle_message(
        SimpleNamespace(payload=json.dumps({
            "config": {
                "availability": {"enabled": True},
                "device_options": {"retain": True},
            }
        }))
    )
    assert a_delete == [True]
    assert r_delete == []
