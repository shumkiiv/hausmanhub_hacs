"""Physical READ may finish while sensors change; decisions use fresh inputs.

Only the external command boundary is captured. Room processing, observation
consumption, guards, ownership and the physical-read adapter remain real.
"""

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import tests.test_scheduled_morning_refresh as morning
from custom_components.hausman_hub.adapters.lighting_mqtt_refresh import READ_CHANNELS
from custom_components.hausman_hub.application.room_lighting_runtime import AWAY_ENTITY_ID
from custom_components.hausman_hub.domain.room_lighting import (
    CurveParams, RoomSensor, SensorKind, config_from_payload,
)
from custom_components.hausman_hub.domain.room_lighting_engine import (
    DecisionReason, LightAction, curve_params_block,
)
from custom_components.hausman_hub.domain.room_lighting_ownership import SensorState
from tests.test_lighting_mqtt_observations import ROLLOUT_BINDINGS, feed, report
from tests.test_stale_auto_refresh import auto_case
from tests.test_stale_handoff_refresh import stale_case

PRESENCE = "binary_sensor.demo_presence"
MOTION = "binary_sensor.test_motion"
LUX = "sensor.test_lux"


def timestamp(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc)


class CaptureExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, hass, command, *, automatic=True, dispatch_guard=None,
                      confirmation_read=False, room_id=None):
        if dispatch_guard is not None and not dispatch_guard():
            return {"confirmed": False, "sent": False, "sendOutcome": "not_sent"}
        self.calls.append(command)
        return {"confirmed": False, "sent": False, "sendOutcome": "not_sent",
                "reason": "test_capture_only"}


async def prepared_case(monkeypatch, ownership):
    factory = morning._make_runtime

    def configured_factory(*args, **kwargs):
        config = config_from_payload(kwargs["payload"])
        config = replace(
            config,
            curve=CurveParams(presence_confirm_seconds=3),
            devices=replace(config.devices, sensors=config.devices.sensors + (
                RoomSensor(id="test_motion", name="Motion", kind=SensorKind.MOTION,
                           entity_id=MOTION),
                RoomSensor(id="test_lux", name="Lux", kind=SensorKind.ILLUMINANCE,
                           entity_id=LUX),
            )),
        )
        assert curve_params_block(config) is None
        kwargs["payload"] = config.to_dict()
        return factory(*args, **kwargs)

    def prepare(runtime, config, clock):
        runtime._hass.states.set(MOTION, "off", last_changed=timestamp(clock["now"]))
        runtime._hass.states.set(LUX, "0", last_changed=timestamp(clock["now"]))

    with monkeypatch.context() as patch:
        patch.setattr(morning, "_make_runtime", configured_factory)
        case = await (auto_case if ownership == "auto" else stale_case)(prepare=prepare)
    runtime, config, clock, *_ = case
    runtime._process_room_locked = type(runtime)._process_room_locked.__get__(runtime, type(runtime))
    runtime._executor = CaptureExecutor()
    context = await runtime._build_context(config)
    runtime._observe_curve_presence(config, context, clock["now"])
    clock["now"] += 600_000
    context = await runtime._build_context(config)
    _, absence = runtime._observe_curve_presence(config, context, clock["now"])
    assert absence >= 600
    return case


class WaitingThirdRead:
    """Delay the report, not publish(), until the real adapter awaits a report."""

    def __init__(self, case):
        self.runtime, self.config, self.clock, self.port, self.native, self.calls, *_ = case
        self.waiting, self.release = asyncio.Event(), asyncio.Event()
        self.pending = None
        self.session = self.port.read_state(ROLLOUT_BINDINGS[3].entity_id).observation.session
        refresher = self.runtime._observation_refresher
        refresher._publish, refresher._pause = self.publish, self.pause
        self.task = None

    def deliver(self, entity, channel):
        self.native[entity].state = "on"
        self.clock["now"] += 10
        feed(self.port, report(self.clock, channel=channel, session=self.session,
                              sequence=self.port._sequence + 1, value="ON"))

    async def publish(self, topic, payload):
        self.calls.append((topic, json.loads(payload)))
        entity = next(row[0] for row in READ_CHANNELS
                      if (row[1], {row[2]: ""}) == self.calls[-1])
        channel = next(index for index, binding in enumerate(ROLLOUT_BINDINGS)
                       if binding.entity_id == entity)
        if len(self.calls) == 3:
            self.pending = entity, channel
            return
        self.deliver(entity, channel)

    async def pause(self, _seconds):
        assert self.pending is not None
        self.waiting.set()
        await self.release.wait()
        if self.pending is not None:
            pending, self.pending = self.pending, None
            self.deliver(*pending)

    async def start(self):
        self.task = asyncio.create_task(self.runtime.async_process([self.config.room_id]))
        await asyncio.wait_for(self.waiting.wait(), 1)
        assert len(self.calls) == 3
        assert not self.runtime._executor.calls

    async def finish(self):
        self.release.set()
        await asyncio.wait_for(self.task, 2)

    async def close(self):
        self.release.set()
        if self.task is not None and not self.task.done():
            self.task.cancel()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)
        await self.runtime.stop()


def sensor(context, sensor_id):
    return next(value for value in context.sensors if value.sensor_id == sensor_id)


def no_stale_absence_commands(runtime, config):
    # Legacy absence and ordinary schedule commands share the same reason.
    assert config.dimming.target_percent == 0
    assert not any(command.target_id == "light_main"
                   and command.brightness == config.dimming.target_percent
                   for command in runtime._executor.calls)
    assert all(command.action is not LightAction.TURN_OFF for command in runtime._executor.calls)


@pytest.mark.parametrize("ownership", ["handoff", "auto"])
@pytest.mark.parametrize("change", ["presence_on", "unknown", "motion", "same_off", "lux"])
async def test_sensor_changes_during_wait_use_fresh_room_decision(monkeypatch, caplog, ownership, change):
    case = await prepared_case(monkeypatch, ownership)
    runtime, config, clock, _, _, calls, *_ = case
    read = WaitingThirdRead(case)
    records = runtime._ownership.snapshots()
    before = await runtime._build_context(config)
    old_presence = sensor(before, "sensor_demo_presence")
    try:
        await read.start()
        # A concurrent tick cannot use the first two reports or publish again.
        await runtime.async_process([config.room_id])
        assert len(calls) == 3 and not runtime._executor.calls
        clock["now"] += 1
        if change in {"presence_on", "unknown"}:
            runtime._hass.states.set(PRESENCE, "on" if change == "presence_on" else "unknown",
                                     last_changed=timestamp(clock["now"]))
        elif change == "motion":
            runtime._hass.states.set(MOTION, "on", last_changed=timestamp(clock["now"]))
        elif change == "same_off":
            # Preserve the state-change date, renew only the observation date.
            runtime._hass.states.get(PRESENCE).last_reported = timestamp(clock["now"])
        else:
            runtime._hass.states.set(LUX, "17", last_changed=timestamp(clock["now"]))
        await read.finish()
        assert len(calls) == 4
        assert runtime._adjustment_refresh_attempts.get(config.room_id) is None
        assert runtime._ownership.snapshots() == records
        context = await runtime._build_context(config)
        if change == "presence_on":
            sample = sensor(context, "sensor_demo_presence")
            assert sample.state is SensorState.ON
            assert sample.continuous_absence_since is None
            assert runtime._curve_presence[config.room_id]["legacy_absence_since"] is None
            assert config.room_id not in runtime._legacy_curve_absence_tasks
            no_stale_absence_commands(runtime, config)
            assert not any(command.brightness is not None for command in runtime._executor.calls)
            clock["now"] = sample.last_changed + 2999
            await runtime.async_process([config.room_id])
            assert not any(command.brightness is not None for command in runtime._executor.calls)
            clock["now"] += 1
            await runtime.async_process([config.room_id])
            assert any(command.target_id == "light_main" and command.brightness == 100
                       and command.reason is DecisionReason.SCHEDULE
                       for command in runtime._executor.calls)
        elif change == "unknown":
            assert sensor(context, "sensor_demo_presence").state is SensorState.UNKNOWN
            assert runtime._curve_presence[config.room_id]["absence"] == 0
            assert runtime._curve_presence[config.room_id]["legacy_absence_since"] is None
            no_stale_absence_commands(runtime, config)
        elif change == "motion":
            assert sensor(context, "test_motion").state is SensorState.ON
            assert runtime._curve_presence_state(context) is SensorState.ON
            assert runtime._curve_presence[config.room_id]["absence"] == 0
            no_stale_absence_commands(runtime, config)
        elif change == "same_off":
            sample = sensor(context, "sensor_demo_presence")
            assert sample.state is SensorState.OFF
            assert sample.last_changed == old_presence.last_changed
            assert sample.observed_at_ms > old_presence.observed_at_ms
        else:
            assert sensor(context, "test_lux").lux == 17
        for _ in range(3):
            await runtime.async_process([config.room_id])
        assert len(calls) == 4
        assert "room lighting command failed" not in caplog.text
        assert "room lighting evaluation failed" not in caplog.text
        assert "ModuleNotFoundError" not in caplog.text
    finally:
        await read.close()


@pytest.mark.parametrize("ownership", ["handoff", "auto"])
@pytest.mark.parametrize("change", ["manual_on", "manual_off", "away_unknown",
                                   "disconnect", "generation", "timeout"])
async def test_real_wait_cancels_for_authority_or_receiver_failure(monkeypatch, caplog, ownership, change):
    case = await prepared_case(monkeypatch, ownership)
    runtime, config, clock, port, _, calls, _, authority = case
    read = WaitingThirdRead(case)
    try:
        await read.start()
        if change.startswith("manual_"):
            await authority.async_capture(ROLLOUT_BINDINGS[3].entity_id,
                                          change.removeprefix("manual_"), "service", "during-read")
        elif change == "away_unknown":
            assert runtime._automatic_commands_enabled(config)
            runtime._hass.states.set(AWAY_ENTITY_ID, "unknown", last_changed=timestamp(clock["now"]))
            assert not runtime._automatic_commands_enabled(config)
        elif change == "disconnect":
            port.connection_changed(False)
        elif change == "generation":
            port.invalidate("synthetic_boundary")
        records = runtime._ownership.snapshots()
        if change == "timeout":
            # Keep the genuine bounded adapter timeout, do not return a fake False.
            await asyncio.wait_for(read.task, 10)
        else:
            await read.finish()
        assert len(calls) == 3
        assert runtime._adjustment_refresh_attempts.get(config.room_id) is not None
        assert not runtime._executor.calls
        assert runtime._ownership.snapshots() == records
        for _ in range(3):
            await runtime.async_process([config.room_id])
        assert len(calls) == 3 and not runtime._executor.calls
        assert runtime._ownership.snapshots() == records
        assert "room lighting command failed" not in caplog.text
        assert "room lighting evaluation failed" not in caplog.text
    finally:
        await read.close()
